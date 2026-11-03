"""controller-side immutable, incremental pod snapshots; standard library only."""
import settings
import storage
import base64
import concurrent.futures
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import select
import shlex
import shutil
import stat
import subprocess
import sys
import time
import uuid
import zlib

import harvest_reader

UTC = dt.timezone.utc


class PodBusy(Exception):
    pass
MAX_BYTES = harvest_reader.MAX_BYTES
DISK_FLOOR_BYTES = 28_000_000_000  # Free-space guard before full snapshots.
LOW_DISK_MAX_BYTES = 50_000_000


def validate(pod, required=False):
    roots, dest = pod.get('results'), pod.get('harvest_dest')
    if required and not roots:
        raise ValueError('--results needs explicit /workspace/DIR paths')
    if roots is not None:
        if not isinstance(roots, list) or any(not harvest_reader.valid(p) for p in roots):
            raise ValueError('results must be explicit normalized /workspace/DIR paths')
        if len(set(roots)) != len(roots) or any(a != b and a.startswith(b + '/') for a in roots for b in roots):
            raise ValueError('overlapping results paths')
    if required or dest is not None:
        if not isinstance(dest, str) or not Path(dest).is_absolute() or '..' in Path(dest).parts or dest == '/':
            raise ValueError('harvest_dest must be an absolute controller lane results path')
    if pod.get('ends_utc') is not None:
        timestamp(pod['ends_utc'])


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('timestamp must be ISO-8601 text with timezone')
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('ends_utc needs a timezone')
    return parsed.astimezone(UTC)


def state_root(root):
    return settings.path('RH_HARVEST_STATE', Path(root) / '.harvest-state')


def rows(path):
    if not path.exists():
        return []
    result = []
    for line in path.read_text().splitlines():
        try:
            result.append(json.loads(line))
        except ValueError:
            continue  # incomplete crash-tail is not evidence
    return result


def append(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(row, sort_keys=True) + '\n')
        f.flush()
        os.fsync(f.fileno())


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.' + path.name + '-' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('x') as f:
            json.dump(value, f, indent=2, sort_keys=True)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
        sync_dir(path.parent)
    finally:
        if temp.exists():
            temp.unlink()  # this call's temporary file


def sync_dir(path):
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Proxy:
    """PTY proxy protocol; base64 frames survive terminal CRLF and echoed input.

    No scp/sftp, remote uploads, terminal changes, environment reads or writes.
    Only an in-memory Python reader and read-only ps sleep inventory run remotely.
    """
    def __init__(self, pod, timeout=600):
        self.deadline = time.monotonic() + timeout
        self.buffer = b''
        self.token = ('RHH_' + uuid.uuid4().hex + ':').encode()
        hosts = settings.path('RH_KNOWN_HOSTS', Path('.runtime/ssh/known_hosts'))
        argv = ['/usr/bin/ssh', '-tt', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20',
                '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=' + str(hosts),
                '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2',
                '-i', str(settings.path('RH_SSH_KEY', Path('.runtime/ssh/id_ed25519'))), pod['ssh_target']]
        self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            while b'# ' not in self.buffer:
                self.read()
            self.buffer = b''
            source = Path(harvest_reader.__file__).read_text() + '\nserve(' + repr(self.token.decode()) + ', ' + repr(pod['results']) + ')\n'
            encoded = base64.b64encode(zlib.compress(source.encode(), 9)).decode()
            # A short shell command starts an in-memory loader. Acknowledging
            # every small input line prevents PTY canonical-queue overflow even
            # on macOS (and avoids depending on shell continuation prompts).
            def frame(value):
                return repr('\n' + self.token.decode() + base64.b64encode(json.dumps(value).encode()).decode())
            loader = ("import base64,zlib,sys\n"
                      "print(" + frame({'boot': True}) + ",flush=True)\n"
                      "chunks=[]\n"
                      "for line in sys.stdin:\n"
                      " if line.strip()=='.':break\n"
                      " chunks.append(line.strip())\n"
                      " print(" + frame({'chunk': True}) + ",flush=True)\n"
                      "exec(zlib.decompress(base64.b64decode(''.join(chunks))))\n")
            self.send('python3 -B -c ' + shlex.quote(loader))
            if self.receive() != {'boot': True}:
                raise ValueError('reader loader startup')
            for i in range(0, len(encoded), 512):
                self.send(encoded[i:i+512])
                if self.receive() != {'chunk': True}:
                    raise ValueError('reader loader chunk')
            self.send('.')
            if self.receive() != {'ready': True}:
                raise ValueError('reader startup')
        except BaseException:
            self.close()
            raise

    def read(self):
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError('harvest timeout')
        if select.select([self.process.stdout], [], [], min(left, .25))[0]:
            block = os.read(self.process.stdout.fileno(), 65536)
            if not block:
                raise OSError('SSH closed')
            self.buffer += block.replace(b'\r', b'')
            if len(self.buffer) > 1_048_576:
                raise ValueError('proxy frame limit')

    def send(self, value):
        pending = memoryview((value + '\n').encode())
        fd = self.process.stdin.fileno()
        os.set_blocking(fd, False)
        while pending:
            left = self.deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError('harvest send timeout')
            readable, writable, _ = select.select([self.process.stdout], [fd], [], min(left, .25))
            if readable:
                self.read()
            if writable:
                try:
                    count = os.write(fd, pending[:1024])
                    pending = pending[count:]
                except BlockingIOError:
                    pass

    def receive(self):
        while True:
            if b'\n' not in self.buffer:
                self.read()
                continue
            line, self.buffer = self.buffer.split(b'\n', 1)
            if line.startswith(self.token):
                return json.loads(base64.b64decode(line[len(self.token):], validate=True))

    def request(self, request):
        encoded = base64.b64encode(json.dumps(request).encode()).decode()
        if len(encoded) > 512:
            for i in range(0, len(encoded), 512):
                self.send('+' + encoded[i:i+512])
                if self.receive() != {'request_chunk': True}:
                    raise ValueError('request chunk acknowledgement')
            self.send('.')
        else:
            self.send(encoded)
        while True:
            row = self.receive()
            yield row
            if row.get('end'):
                return

    def manifest(self):
        operation = 'manifest'
        while True:
            # Drain each bounded page before yielding: the caller can then get
            # files on this same connection while the inventory is paused.
            page = list(self.request(dict(op=operation)))
            end = page.pop()
            if end.get('deadline_seconds') is not None:
                yield dict(metadata=True, deadline_seconds=end['deadline_seconds'])
            yield from page
            if end.get('error') or not end.get('more'):
                yield dict(end=True, **({'error': end['error']} if end.get('error') else {}))
                return
            operation = 'next'


    def download(self, item, target):
        ended = False
        for row in self.request(dict(op='get', **item)):
            if row.get('error'):
                raise ValueError('remote file changed or unreadable')
            if 'data' in row:
                target.write(base64.b64decode(row['data'], validate=True))
                if target.tell() > MAX_BYTES:
                    raise ValueError('size guard')
            if row.get('end'):
                ended = row.get('size') == item['size'] and row.get('sha256') == item['sha256']
        if not ended:
            raise ValueError('incomplete transfer')

    def close(self):
        p = self.process
        if p.poll() is None:
            try:
                self.send(base64.b64encode(b'{"op":"quit"}').decode())
                self.send('exit')
                p.wait(timeout=2)
            except (OSError, subprocess.SubprocessError):
                p.kill()  # local SSH child only
        p.wait()
        p.stdin.close()
        p.stdout.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def digest(path):
    h, size = hashlib.sha256(), 0
    with path.open('rb') as f:
        while True:
            block = f.read(262144)
            if not block:
                break
            size += len(block)
            h.update(block)
    return size, h.hexdigest()


def local_path(base, relative):
    path = base / relative
    if Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ValueError('unsafe local path')
    # Refuse symlinks even if they point within the destination tree.
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError('local symlink')
    return path


def scope(pod):
    return hashlib.sha256(json.dumps([pod.get('results'), pod.get('harvest_dest')], sort_keys=True).encode()).hexdigest()


def pod_identity(row, pod):
    return (row.get('pod') == pod['name'] and row.get('pod_id') == pod['pod_id']
            and row.get('lane') == pod['lane'])


def identity(row, pod):
    return pod_identity(row, pod) and row.get('scope') == scope(pod)


def snapshots(base, pod):
    """Only committed manifests for this pod and declaration are evidence."""
    found = []
    for directory in sorted(local_path(base, 'snapshots').iterdir()):
        try:
            manifest = local_path(base, str(directory.relative_to(base)) + '/MANIFEST.json')
            record = json.loads(manifest.read_text())
            if identity(record, pod) and isinstance(record.get('files_index'), dict):
                timestamp(record['utc'])
                if type(record.get('snapshot_created_ns', 0)) is not int:
                    continue
                found.append((directory, record))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue  # malformed/unfinished snapshots are never pruning candidates
    return sorted(found, key=lambda entry: (timestamp(entry[1]['utc']),
                  entry[1].get('snapshot_created_ns', 0), entry[0].name))


def low_disk_alert(root, pod, row):
    path = local_path(Path(root), 'alerts/harvest-low-disk-' + pod['name'])
    day = timestamp(row['utc']).date().isoformat()
    try:
        if json.loads(path.read_text()).get('day') == day:
            return
    except (FileNotFoundError, ValueError):
        pass
    atomic(path, dict(day=day, utc=row['utc'], pod=pod['name'],
                      free_bytes=row['free_bytes'], floor_bytes=DISK_FLOOR_BYTES,
                      message='Harvest restricted to new/changed files under 50 MB; snapshot unverified'))


def check_disk(root, pod, base, row):
    row['free_bytes'] = shutil.disk_usage(base).free
    if row['free_bytes'] < DISK_FLOOR_BYTES:
        row['low_disk'] = True
        low_disk_alert(root, pod, row)


def snapshot_inventory(base, directory):
    """Hash every physical payload; refuse links/special files and escapes."""
    inventory = {}
    local_path(base, str(directory.relative_to(base)))
    def failed(error):
        raise error
    for parent, dirs, files in os.walk(directory, followlinks=False, onerror=failed):
        for name in dirs + files:
            path = local_path(base, str((Path(parent) / name).relative_to(base)))
            mode = path.lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise ValueError('nonregular snapshot entry')
        for name in files:
            path = Path(parent) / name
            if path == directory / 'MANIFEST.json':
                continue
            inventory[str(path.relative_to(directory))] = digest(path)
    return inventory


def retain(base, pod, log, now, index):
    """Prune only redundant payloads, keeping two verified snapshots and all unique data.

    Legacy manifests may reference other snapshots: those dependencies remain
    protected even when their payload is duplicated elsewhere.
    """
    # This is the sole non-temporary removal permitted by harvest; it uses Trash. Refuse any
    # destination outside the explicitly authorized pod-harvest/*/snapshots tree.
    if base.parent.name != 'pod-harvest' or base.name != pod['name']:
        return
    records = snapshots(base, pod)
    verified = [(p, r) for p, r in records if r.get('verified') is True]
    if len(verified) < 3:
        return
    kept = verified[-2:]
    coverage = {}
    for directory, record in kept:
        # Only the new self-contained format can prove coverage. Rehash now:
        # an old verified flag alone cannot establish that bytes still exist.
        if record.get('snapshot_format') != 2:
            continue
        inventory = snapshot_inventory(base, directory)
        expected = {source.lstrip('/'): (item['size'], item['sha256'])
                    for source, item in record['files_index'].items()}
        if inventory != expected:
            return
        for path, value in inventory.items():
            coverage.setdefault(path, set()).add(value)
    for directory, record in verified[:-2]:
        prefix = str(directory.relative_to(base)) + '/'
        # Include manifests of other scopes/pod generations, failed runs, and
        # the retained index. Never leave any surviving metadata dangling.
        dependent = False
        for manifest in local_path(base, 'snapshots').glob('*/MANIFEST.json'):
            if manifest.parent == directory:
                continue
            try:
                other = json.loads(local_path(base, str(manifest.relative_to(base))).read_text())
                if any(v.get('stored', '').startswith(prefix) for v in other['files_index'].values()):
                    dependent = True
                    break
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                dependent = True
                break
        if dependent or any(v.get('stored', '').startswith(prefix) for v in index.values()):
            continue
        inventory = snapshot_inventory(base, directory)
        if any(value not in coverage.get(path, set()) for path, value in inventory.items()):
            continue
        if any((item['size'], item['sha256']) not in coverage.get(source.lstrip('/'), set())
               for source, item in record['files_index'].items()):
            continue  # legacy logical inventories also need complete coverage
        event = dict(utc=now, pod=pod['name'], pod_id=pod['pod_id'], lane=pod['lane'],
                     scope=scope(pod), path=str(directory), files=len(inventory),
                     bytes=sum(value[0] for value in inventory.values()))
        append(log, dict(event, event='SNAPSHOT_REMOVE_STARTED'))
        storage.trash(directory)
        sync_dir(directory.parent)
        append(log, dict(event, event='SNAPSHOT_REMOVED'))


def notify(pod):
    try:
        return subprocess.run([*settings.command('RH_NOTIFY_COMMAND', 'notify-agent'),
            'Pod ' + pod['name'] + ' results not copied for 30 min', 'Pod harvest failed'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def alert(path, pod, row):
    history = [r for r in rows(path) if pod_identity(r, pod)]
    runs = [r for r in history if identity(r, pod) and not r.get('event')]
    if len(runs) < 2 or any(r.get('verified') is True for r in runs[-2:]):
        return
    now = timestamp(row['utc'])
    if any(r.get('event') == 'NOTIFIED' and now - timestamp(r['utc']) < dt.timedelta(hours=6) for r in history):
        return
    if notify(pod):
        append(path, dict(utc=row['utc'], pod=pod['name'], pod_id=pod['pod_id'], lane=pod['lane'], scope=scope(pod), event='NOTIFIED'))
    else:
        print('rh pods: harvest notification failed for ' + pod['name'], file=sys.stderr)


def harvest_one(root, pod, rh, transport=None, reason='periodic'):
    # Keep enough identity and a fallback timestamp to log even initialization
    # failures. Registry/preflight errors are handled before an attempt starts.
    row = dict(utc=dt.datetime.now(UTC).isoformat(), pod=pod.get('name'),
               pod_id=pod.get('pod_id'), lane=pod.get('lane'), files=0, bytes=0,
               verified=False, errors=[], skipped=[], unchanged=0, reason=reason, scope=None)
    row.update(linked_files=0, linked_bytes=0, snapshot_files=0, snapshot_bytes=0,
               verified_files=0, verified_bytes=0, skipped_low_disk=[], low_disk=False,
               bytes_written=0)
    log = state_root(root) / 'HARVEST.jsonl'
    try:
        transport = transport or Proxy
        now = rh.now()
        row.update(utc=now, scope=scope(pod))
        validate(pod, required=True)
        base = Path(pod['harvest_dest']) / pod['name']
        local_path(base, 'snapshots').mkdir(parents=True, exist_ok=True)
        with (base / '.harvest.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise PodBusy()
            index_file = local_path(base, 'INDEX.json')
            index = json.loads(index_file.read_text()) if index_file.exists() else {}
            if index.get('pod_id') != pod['pod_id']:
                index = {}
            latest = index.get('files', {})
            previous = [(p, r) for p, r in snapshots(base, pod) if r.get('verified') is True]
            prior = previous[-1][1]['files_index'] if previous else {}
            row['previous_verified_snapshot'] = str(previous[-1][0]) if previous else None
            snapshot = 'snapshots/' + timestamp(now).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex[:8]
            row['snapshot_created_ns'] = time.time_ns()
            row['snapshot'] = str(base / snapshot)
            check_disk(root, pod, base, row)
            current = {}
            complete, count = False, 0
            # Copy as pages arrive, so a large first inventory cannot postpone
            # every useful byte until the whole tree has been hashed.
            with transport(pod) as remote:
                try:
                    for item in remote.manifest():
                        if item.get('metadata') or item.get('end'):
                            seconds = item.get('deadline_seconds')
                            if type(seconds) is int and seconds > 0:
                                row['discovered_ends_utc'] = (timestamp(rh.now()) + dt.timedelta(seconds=seconds)).isoformat()
                            if item.get('end'):
                                if item.get('error'):
                                    row['errors'].append(dict(error='manifest_failed'))
                                else:
                                    complete = True
                            continue
                        count += 1
                        if count > 100000:
                            raise ValueError('manifest file limit')
                        source = item.get('path', '')
                        if not harvest_reader.valid(source) or not any(source.startswith(p + '/') or source == p for p in pod['results']):
                            raise ValueError('unsafe manifest path')
                        if item.get('skip'):
                            row['skipped'].append(item)
                            continue
                        if item.get('error'):
                            row['errors'].append(item)
                            continue
                        if (type(item.get('size')) is not int or not 0 <= item['size'] <= MAX_BYTES
                              or not re.fullmatch('[a-f0-9]{64}', item.get('sha256', ''))):
                            raise ValueError('invalid file manifest')
                        if source in current:
                            raise ValueError('duplicate manifest path')
                        old = prior.get(source, {})
                        expected = (item['size'], item['sha256'])
                        unchanged = False
                        try:
                            if ((old.get('size'), old.get('sha256')) == expected
                                    and old['stored'].startswith('snapshots/')
                                    and digest(local_path(base, old['stored'])) == expected):
                                unchanged = True
                        except FileNotFoundError:
                            pass
                        check_disk(root, pod, base, row)
                        if row['low_disk'] and (unchanged or item['size'] >= LOW_DISK_MAX_BYTES):
                            row['skipped_low_disk'].append(dict(item, skip='skipped_low_disk'))
                            continue
                        relative = snapshot + '/' + source.lstrip('/')
                        target = local_path(base, relative)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        temporary = target.with_name('.' + target.name + '-' + uuid.uuid4().hex + '.tmp')
                        try:
                            linked = False
                            if unchanged:
                                try:
                                    os.link(local_path(base, old['stored']), temporary)
                                    linked = True
                                except OSError:
                                    pass  # e.g. unsupported filesystem: verified copy fallback
                            if not linked:
                                with temporary.open('xb') as f:
                                    try:
                                        remote.download(item, f)
                                    finally:
                                        row['bytes_written'] += f.tell()
                                    f.flush()
                                    os.fsync(f.fileno())
                            if digest(temporary) != expected:
                                raise ValueError('size/hash verification failed')
                            os.replace(temporary, target)
                            sync_dir(target.parent)
                            latest[source] = dict(item, stored=relative)
                            current[source] = dict(item, stored=relative)
                            row['snapshot_files'] += 1
                            row['snapshot_bytes'] += item['size']
                            if linked:
                                row['unchanged'] += 1
                                row['linked_files'] += 1
                                row['linked_bytes'] += item['size']
                            else:
                                row['files'] += 1
                                row['bytes'] += item['size']
                        except ValueError as error:
                            row['errors'].append(dict(path=source, error=type(error).__name__))
                        finally:
                            if temporary.exists():
                                temporary.unlink()  # this call's uncommitted transfer
                except (OSError, ValueError, KeyError, TypeError) as error:
                    row['errors'].append(dict(error=type(error).__name__))
            if not complete:
                row['errors'].append(dict(error='incomplete_manifest'))
            # Full verification of the final snapshot, including every linked
            # file. Never infer success from an inode, timestamp or prior flag.
            for source, item in current.items():
                if digest(local_path(base, item['stored'])) != (item['size'], item['sha256']):
                    row['errors'].append(dict(path=source, error='snapshot_verification_failed'))
                else:
                    row['verified_files'] += 1
                    row['verified_bytes'] += item['size']
            row['verified'] = complete and not row['errors'] and not row['low_disk']
            record = dict(row, snapshot_format=2, results=pod['results'], files_index=current)
            atomic(local_path(base, snapshot + '/MANIFEST.json'), record)
            atomic(index_file, dict(pod_id=pod['pod_id'], files=latest, last_run=now))
            if row['verified']:
                retain(base, pod, log, now, latest)
    except BaseException as error:
        # A busy pod is an unsuccessful attempt too: it must leave evidence and
        # return a failure status without opening another remote connection.
        row['errors'].append(dict(error=type(error).__name__))
        row['verified'] = False
        if not isinstance(error, Exception):
            # Preserve cancellation after the finally block records it.
            if isinstance(error, SystemExit) and error.code in (None, 0):
                raise SystemExit(1) from error
            raise
    finally:
        try:
            try:
                row['utc'] = rh.now()
            except BaseException as error:
                row['errors'].append(dict(error=type(error).__name__))
                row['verified'] = False
                if not isinstance(error, Exception):
                    if isinstance(error, SystemExit) and error.code in (None, 0):
                        raise SystemExit(1) from error
                    raise
        finally:
            # Exactly one attempt row, including busy, startup and copy failures.
            # An unavailable log must propagate; never claim success without it.
            append(log, row)
    if row['scope'] is not None:
        alert(log, pod, row)
    print(json.dumps(row, sort_keys=True))
    return row


def latest_deadline(pod, history):
    if pod.get('ends_utc'):
        return timestamp(pod['ends_utc'])
    discovered = [r for r in history if identity(r, pod) and r.get('discovered_ends_utc')]
    if not discovered:
        return None
    latest = timestamp(discovered[-1]['discovered_ends_utc'])
    for previous in reversed(discovered[:-1]):
        candidate = timestamp(previous['discovered_ends_utc'])
        if abs((candidate - latest).total_seconds()) > 120:
            break
        latest = candidate
    return latest


def run(root, entries, rh, name=None, due=False, transport=None):
    if name and not any(p['name'] == name for p in entries):
        raise ValueError('unknown pod: ' + name)

    def one(pod):
        reasons = ['manual' if name else 'periodic']
        if due:
            history = rows(state_root(root) / 'HARVEST.jsonl')
            end = latest_deadline(pod, history)
            if end is None:
                return False
            now = timestamp(rh.now())
            reasons = []
            for lead in (20, 5):
                when = end - dt.timedelta(minutes=lead)
                key = 'deadline-' + end.replace(microsecond=0).isoformat() + '-minus-' + str(lead)
                # Late wakeups catch up before destruction. Never act on pods.
                if when <= now < end and not any(identity(r, pod) and r.get('reason') == key and r.get('verified') for r in history):
                    reasons.append(key)
            if not reasons:
                return False
        failed = False
        for reason in reasons:
            result = harvest_one(root, pod, rh, transport, reason)
            failed |= not result['verified']
        return failed

    selected = [p for p in entries if not name or p['name'] == name]
    # Per-pod lock also serializes manual/deadline runs. Independent pods cannot
    # consume each other's ten-minute transport timeout or delay their copies.
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        failures = list(pool.map(one, selected))
    return 1 if any(failures) else 0


def lint(root, entries, rh):
    history = rows(state_root(root) / 'HARVEST.jsonl')
    now = timestamp(rh.now())
    errors = []
    for pod in entries:
        if not pod.get('results'):
            errors.append('pod {} has no results paths'.format(pod['name']))
        if not pod.get('harvest_dest'):
            errors.append('pod {} has no harvest_dest'.format(pod['name']))
        verified = [r for r in history if identity(r, pod) and r.get('verified') is True and not r.get('event')]
        if not verified or not dt.timedelta(0) <= now - timestamp(verified[-1]['utc']) <= dt.timedelta(minutes=45):
            errors.append('pod {} last verified harvest missing or older than 45 min'.format(pod['name']))
    return errors
