"""Lane-independent pod registry, utilization, work scheduling and rental gates (stdlib)."""
import settings
import argparse
import harvest
import cadence
import base64
import contextlib
import hashlib
import inspect
import socket
import stat
import threading
import concurrent.futures
import datetime as dt
import fcntl
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import select
import shlex
import shutil
import subprocess
import sys
import time
import uuid
import zlib

LANES = settings.names('RH_POD_LANES')
UTC = dt.timezone.utc
NAME = r'[a-z0-9][a-z0-9_-]*'


def backup(path):
    dest = Path(str(path) + '.bak-registry')
    if path.exists() and not dest.exists():
        shutil.copy2(path, dest)


def work_queue(value):
    """An explicit ownership declaration; never infer ownership from utilization."""
    if value == 'harness':
        return value
    if isinstance(value, str) and value.startswith('done-file:'):
        path = value[len('done-file:'):]
        if (path.startswith('/') and path != '/' and str(PurePosixPath(path)) == path
                and '..' not in PurePosixPath(path).parts and not path.startswith('//')
                and not any(ord(c) < 32 or ord(c) == 127 for c in path)):
            return value
    raise ValueError('work_queue must be harness or done-file:/absolute/normalized/pod/path')


def done_file_path(pod):
    value = pod.get('work_queue')
    return value[len('done-file:'):] if isinstance(value, str) and value.startswith('done-file:') else None


def registry(root):
    path = root / 'pods.json'
    entries = json.loads(path.read_text()) if path.exists() else []
    if not isinstance(entries, list):
        raise ValueError('pods.json must be an array')
    names, ids, targets = set(), set(), set()
    for pod in entries:
        if not isinstance(pod, dict) or not re.fullmatch(NAME, pod.get('name', '')):
            raise ValueError('invalid pod name')
        if pod.get('lane') not in LANES:
            raise ValueError('pod lane must be configured in RH_POD_LANES')
        if not re.fullmatch(r'[a-z0-9]+', pod.get('pod_id', '')):
            raise ValueError('invalid pod id')
        if not re.fullmatch(r'[a-z0-9-]+@ssh\.runpod\.io', pod.get('ssh_target', '')):
            raise ValueError('ssh target must use the provider proxy syntax')
        for key, low in (('vcpus', 0), ('gpus', -1), ('dollars_per_hour', -1)):
            value = pod.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= low:
                raise ValueError('invalid pod ' + key)
        if int(pod['gpus']) != pod['gpus'] or pod['dollars_per_hour'] < 0:
            raise ValueError('invalid GPU count or hourly rate')
        if not isinstance(pod.get('owner_note'), str) or not pod['owner_note'].strip():
            raise ValueError('owner note is required')
        if pod['name'] in names or pod['pod_id'] in ids or pod['ssh_target'] in targets:
            raise ValueError('duplicate pod name, id or SSH target')
        harvest.validate(pod)
        if 'keep' in pod and type(pod['keep']) is not bool:
            raise ValueError('pod keep must be boolean')
        if pod.get('work_queue') is not None:
            work_queue(pod['work_queue'])
        if done_file_path(pod):
            harvest.validate(pod, required=True)
        names.add(pod['name']); ids.add(pod['pod_id']); targets.add(pod['ssh_target'])
    return entries


def save_registry(root, entries):
    path = root / 'pods.json'
    backup(path)
    # Atomic replacement, so concurrent read-only lint sees a complete registry.
    temporary = root / ('.pods-' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(entries, indent=2) + '\n')
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()  # temporary file created by this invocation


def allowance(nproc, cpu_max):
    count = float(nproc.strip())
    quota, period = cpu_max.split()
    if quota not in ('max', '-1'):
        if float(period) <= 0 or float(quota) <= 0:
            raise ValueError('invalid CPU quota')
        count = min(count, float(quota) / float(period))
    if not math.isfinite(count) or count <= 0:
        raise ValueError('invalid CPU allowance')
    return count


def cpu_fraction(before, after, elapsed, vcpus):
    if elapsed <= 0 or vcpus <= 0 or after < before:
        raise ValueError('invalid CPU counter interval')
    return (after - before) / (elapsed * 1_000_000 * vcpus)


def remote_read(pod, timeout=60):
    """One proxy PTY; read-only metrics plus an in-memory done-file stat reader.

    Never upload a script or change terminal settings.
    All waiting is local; the two cpu.stat reads are five seconds apart. Never
    read /proc/1/environ or expose raw SSH output (including login banners).
    """
    key = settings.path('RH_SSH_KEY', Path('.runtime/ssh/id_ed25519'))
    hosts = settings.path('RH_KNOWN_HOSTS', Path('.runtime/ssh/known_hosts'))
    argv = ['/usr/bin/ssh', '-tt', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=' + str(hosts),
            '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2',
            '-i', str(key), pod['ssh_target']]
    deadline = time.monotonic() + min(timeout, 60)
    p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    data = b''
    nonce = 'RH_' + uuid.uuid4().hex

    def until(token):
        nonlocal data
        while token not in data:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError('pod probe timed out')
            if select.select([p.stdout], [], [], min(left, 0.25))[0]:
                block = os.read(p.stdout.fileno(), 65536)
                if not block:
                    raise OSError('pod SSH closed')
                data += block.replace(b'\r', b'')
                if len(data) > 262144:
                    raise ValueError('pod probe output exceeded limit')

    def send(command):
        command = command.replace('/sys/fs/cgroup', shlex.quote(os.environ.get('RH_REMOTE_CGROUP_ROOT', '/sys/fs/cgroup')))
        command = command.replace('/proc/uptime', shlex.quote(os.environ.get('RH_REMOTE_UPTIME', '/proc/uptime')))
        p.stdin.write((command + '\n').encode())
        p.stdin.flush()

    def mark(label):
        return "printf '\\n%s\\n' '" + nonce + '_' + label + "'"

    try:
        until(b'# ')
        # One command line: terminal echo contains command text, never a marker
        # on its own line, so it cannot be mistaken for actual measurement data.
        layout = (
            'if [ -r /sys/fs/cgroup/cpu.stat ]; then '
            'rh_mode=v2; rh_stat=/sys/fs/cgroup/cpu.stat; '
            'else rh_mode=v1; rh_stat=/sys/fs/cgroup/cpuacct/cpuacct.usage; '
            'rh_quota=/sys/fs/cgroup/cpu; '
            'for rh_dir in /sys/fs/cgroup/cpuacct /sys/fs/cgroup/cpu '
            '/sys/fs/cgroup/cpuacct,cpu /sys/fs/cgroup/cpu,cpuacct; do '
            'if [ -r "$rh_dir/cpuacct.usage" ]; then rh_stat="$rh_dir/cpuacct.usage"; fi; '
            'if [ -r "$rh_dir/cpu.cfs_quota_us" ]; then rh_quota="$rh_dir"; fi; done; fi')
        parts = ["export PS1='' PS2=''; unset HISTFILE PROMPT_COMMAND", layout,
                 mark('MODE'), 'printf \'%s\\n\' "$rh_mode"', mark('NPROC'), 'nproc', mark('MAX'),
                 'if [ "$rh_mode" = v2 ]; then cat /sys/fs/cgroup/cpu.max; '
                 'else cat "$rh_quota/cpu.cfs_quota_us" "$rh_quota/cpu.cfs_period_us"; fi', mark('GPUS'),
                 "if command -v nvidia-smi >/dev/null; then nvidia-smi -L; else printf 'NO_GPU\\n'; fi",
                 mark('TIME1'), 'cat /proc/uptime', mark('CPU1'),
                 'cat "$rh_stat"', mark('WAIT')]
        send('; '.join(parts))
        until(('\n' + nonce + '_WAIT\n').encode())
        if deadline - time.monotonic() < 5:
            raise TimeoutError('pod probe timed out')
        time.sleep(5)
        final_parts = [mark('TIME2'), 'cat /proc/uptime', mark('CPU2'),
                       'cat "$rh_stat"', mark('GPUUTIL'),
                       'nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null']
        if done_file_path(pod):
            # In-memory stdlib reader; -B forbids bytecode writes. Paths are data,
            # base64 encodes the program and shlex quotes its one shell argument.
            final_parts += [mark('DONEFILE'), done_file_command(pod)]
        send('; '.join(final_parts + [mark('END'), 'exit']))
        until(('\n' + nonce + '_END\n').encode())
        output = data.decode('utf-8', 'replace')
        labels = ('MODE', 'NPROC', 'MAX', 'GPUS', 'TIME1', 'CPU1', 'WAIT', 'TIME2', 'CPU2', 'GPUUTIL', 'END')
        if done_file_path(pod):
            labels = labels[:-1] + ('DONEFILE', 'END')
        fields = {}
        for a, b in zip(labels, labels[1:]):
            # WAIT contains the echoed second command; it is never parsed.
            fields[a] = output.split('\n' + nonce + '_' + a + '\n', 1)[1].split(
                '\n' + nonce + '_' + b + '\n', 1)[0].strip()
        return fields
    finally:
        # This kills/reaps the local SSH child only, never a pod worker/timer.
        if p.poll() is None:
            p.kill()
        p.wait()
        p.stdin.close()
        p.stdout.close()


def probe(pod):
    fields = remote_read(pod, timeout=60)
    vcpus = allowance(fields['NPROC'], fields['MAX'])
    devices = fields['GPUS'].splitlines()
    gpus = sum(bool(re.match(r'^GPU \d+:', line)) for line in devices)
    if not gpus and fields['GPUS'] not in ('NO_GPU', 'No devices were found'):
        raise ValueError('GPU inventory unavailable')
    if pod.get('gpus', 0) > 0 and not gpus:
        raise ValueError('registered GPU unavailable')
    gpu_util = None
    if gpus:
        values = [float(v.strip()) for v in fields['GPUUTIL'].splitlines() if v.strip()]
        if len(values) != gpus or any(not math.isfinite(v) or not 0 <= v <= 100 for v in values):
            raise ValueError('GPU utilization unavailable')
        gpu_util = max(values)
    if fields.get('MODE', 'v2') == 'v1':
        counters = [int(fields[label]) / 1000 for label in ('CPU1', 'CPU2')]
    else:
        counters = [int(re.search(r'^usage_usec (\d+)$', fields[label], re.M)[1]) for label in ('CPU1', 'CPU2')]
    elapsed = float(fields['TIME2'].split()[0]) - float(fields['TIME1'].split()[0])
    result = dict(vcpus=vcpus, gpus=gpus, gpu_util=gpu_util,
                  cpu_frac=cpu_fraction(*counters, elapsed, vcpus))
    if done_file_path(pod):
        result['done_file'] = json.loads(fields['DONEFILE'])
    return result


def stamp(value):
    t = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if t.tzinfo is None:
        raise ValueError('pod timestamp needs timezone')
    return t.astimezone(UTC)


def samples(history, pod):
    return sorted((r for r in history if r.get('pod') == pod['name'] and not r.get('event')
                   and r.get('lane') == pod['lane']
                   and r.get('pod_id', pod['pod_id']) == pod['pod_id']), key=lambda r: stamp(r['utc']))


def low(row, pod, threshold):
    cpu, gpu = row.get('cpu_frac'), row.get('gpu_util')
    return (row.get('reachable') is True and isinstance(cpu, (int, float)) and 0 <= cpu < threshold
            and (not row.get('gpus', pod['gpus']) or
                 isinstance(gpu, (int, float)) and 0 <= gpu < threshold * 100))


def state(history, pod):
    recent = samples(history, pod)
    if not recent:
        return False, False
    last = stamp(recent[-1]['utc'])
    # At a 15-minute cadence adjacent samples are too close: select the latest
    # eligible older sample and require every intervening sample to remain low.
    older = next((i for i in range(len(recent) - 2, -1, -1)
                  if last - stamp(recent[i]['utc']) >= dt.timedelta(minutes=25)), None)
    underused = older is not None and all(low(r, pod, .60) for r in recent[older:])
    idle = (len(recent) >= 4 and last - stamp(recent[-4]['utc']) >= dt.timedelta(minutes=45)
            and all(low(r, pod, .05) for r in recent[-4:]))
    return underused, idle


def percentages(row):
    value = 'CPU {:.1f}%'.format(row['cpu_frac'] * 100)
    if row.get('gpu_util') is not None:
        value += ' GPU {:.1f}%'.format(row['gpu_util'])
    return value


def notify(pod, row, kind):
    message = '{} {} {} ${:.2f}/h'.format(pod['name'], pod['lane'], percentages(row), pod['dollars_per_hour'])
    try:
        result = subprocess.run([*settings.command('RH_NOTIFY_COMMAND', 'notify-agent'), message, 'Pod ' + kind],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def alerts(root, history, pod, rh):
    underused, idle = state(history, pod)
    row = samples(history, pod)[-1]
    if idle:
        event = dict(row, event='STOP_RECOMMENDED')
        rh.append(root / 'PODS.jsonl', event)
        history.append(event)
        print(json.dumps(event))
    if not (underused or idle):
        return
    kind = 'idle' if idle else 'underused'
    clock = stamp(row['utc'])
    sent = [r for r in history if r.get('event') == 'NOTIFIED' and r.get('pod') == pod['name']
            and r.get('pod_id') == pod['pod_id'] and r.get('kind') == kind]
    if any(clock - stamp(r['utc']) < dt.timedelta(hours=6) for r in sent):
        return
    if notify(pod, row, kind):
        event = dict(utc=row['utc'], pod=pod['name'], pod_id=pod['pod_id'], lane=pod['lane'],
                     event='NOTIFIED', kind=kind)
        rh.append(root / 'PODS.jsonl', event)
        history.append(event)
    else:
        print('rh pods: notification failed for ' + pod['name'], file=sys.stderr)


def check(root, entries, rh):
    def measure(pod):
        row = dict(utc=rh.now(), pod=pod['name'], pod_id=pod['pod_id'], lane=pod['lane'],
                   cpu_frac=None, gpu_util=None, reachable=False)
        try:
            row.update(probe(pod), reachable=True)
        except (OSError, ValueError, KeyError, TypeError, IndexError, subprocess.SubprocessError):
            pass  # Never persist SSH banners, stderr, exception output or secrets.
        row['utc'] = rh.now()
        return row

    backup(root / 'PODS.jsonl')
    history = rh.rows(root / 'PODS.jsonl')
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        measured = list(pool.map(measure, entries))
    # Persist all samples before notification attempts; one failure cannot erase
    # another pod's measurement or turn an unreachable pod into an idle one.
    for row in measured:
        rh.append(root / 'PODS.jsonl', row)
        history.append(row)
        print(json.dumps(row))
    for pod in entries:
        alerts(root, history, pod, rh)
    return 0


def lint(lane, rh):
    if lane.name not in LANES:
        return []
    root = lane.parent.parent
    entries = [p for p in registry(root) if p['lane'] == lane.name]
    if not entries:
        return []
    history = rh.rows(root / 'PODS.jsonl')
    return harvest.lint(root, entries, rh) + ['underused pod {}: {}'.format(p['name'], percentages(samples(history, p)[-1]))
            for p in entries if state(history, p)[0]]


def arguments(sub):
    actions = sub.add_parser('pods', help='pod monitoring, harvesting and verified shutdown').add_subparsers(dest='action', required=True)
    reap_parser = actions.add_parser('reap', help='reap verified idle pods; rollout defaults to dry-run for 24h')
    reap_parser.add_argument('--dry-run', action='store_true', help='always prevent termination, including in enforce mode')
    plan_parser = actions.add_parser('plan', help='refuse rental without runnable capacity')
    plan_parser.add_argument('lane', choices=sorted(LANES))
    plan_parser.add_argument('--vcpus', type=float, required=True)
    plan_parser.add_argument('--gpu', action='store_true')
    plan_parser.add_argument('--gpus', type=int, default=1)
    enqueue_parser = actions.add_parser('enqueue', help='add an explicitly migrated executable target')
    enqueue_parser.add_argument('lane', choices=sorted(LANES))
    enqueue_parser.add_argument('id')
    enqueue_parser.add_argument('--argv-json', required=True)
    enqueue_parser.add_argument('--cores', type=int, default=4)
    enqueue_parser.add_argument('--gpu-bound', action='store_true')
    enqueue_parser.add_argument('--depends-on', action='append', default=[])
    dispatch_parser = actions.add_parser('dispatch', help='serve on-pod pulls over outbound proxy SSH')
    dispatch_parser.add_argument('name')
    dispatch_parser.add_argument('--destination', type=Path, required=True)
    harvest_parser = actions.add_parser('harvest', help='pull verified incremental result snapshots')
    harvest_parser.add_argument('name', nargs='?')
    harvest_parser.add_argument('--due', action='store_true', help='only due pre-deadline harvests')
    for action in ('add', 'set', 'remove', 'list', 'check', 'busy'):
        p = actions.add_parser(action)
        if action in ('add', 'set', 'remove', 'busy'):
            p.add_argument('name')
        if action in ('add', 'set'):
            p.add_argument('--work-queue', type=work_queue, required=action == 'set',
                           help='harness or done-file:/absolute/pod/path')
        if action == 'add':
            p.add_argument('--keep', action='store_true', help='exclude from idle reaping')
            p.add_argument('--pod-id', required=True)
            p.add_argument('--ssh-target', required=True)
            p.add_argument('--lane', choices=sorted(LANES), required=True)
            p.add_argument('--dollars-per-hour', type=float, required=True)
            p.add_argument('--owner-note', required=True)
            p.add_argument('--results', nargs='+', required=True)
            p.add_argument('--harvest-dest', required=True)
            p.add_argument('--ends-utc', help='known timer deadline, ISO-8601 with timezone')


def main(root, args, rh):
    root = root.expanduser().resolve()
    # Lane delivery must be checked even if pod registry/locking/harvesting fails.
    lint_code = rh.lint_monitor(root) if args.action == 'check' else 0
    cadence_code = cadence.monitor(root, rh) if args.action == 'check' else 0
    if args.action == 'plan':
        if args.gpu and args.gpus <= 0:
            raise ValueError('--gpu requires a positive --gpus count')
        result = plan(root, args.lane, args.vcpus, args.gpus if args.gpu else 0)
        print(result['message'])
        return 0 if result['pass'] else 1
    if args.action == 'enqueue':
        enqueue(root, dict(id=args.id, lane=args.lane, argv=json.loads(args.argv_json),
                           cores=args.cores, gpu_bound=args.gpu_bound,
                           depends_on=args.depends_on, runnable=True))
        return 0
    if args.action == 'dispatch':
        pod = next((p for p in registry(root) if p['name'] == args.name), None)
        if pod is None:
            raise ValueError('unknown pod: ' + args.name)
        return dispatch(root, pod, args.destination)
    if args.action == 'harvest':
        return harvest.run(root, registry(root), rh, name=args.name, due=args.due)
    # list/busy are read-only and never connect to a pod.
    if args.action in ('list', 'busy'):
        entries = registry(root)
        if args.action == 'list':
            for pod in entries:
                print(json.dumps(pod))
            return 0
        pod = next((p for p in entries if p['name'] == args.name), None)
        if pod is None:
            return 1
        recent = samples(rh.rows(root / 'PODS.jsonl'), pod)
        if not recent:
            return 1
        row = recent[-1]
        age = stamp(rh.now()) - stamp(row['utc'])
        if row.get('reachable') is not True or not dt.timedelta(0) <= age <= dt.timedelta(minutes=30):
            return 1
        return 0 if ((row.get('cpu_frac') or 0) >= .60 or
                     row.get('gpus', pod['gpus']) and (row.get('gpu_util') or 0) >= 60) else 1
    with (root / '.pods.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('another pod check or registry edit is running')
        entries = registry(root)
        if args.action == 'check':
            code = check(root, entries, rh)
            harvested = harvest.run(root, entries, rh)
            reaped = reap(root, entries, rh, quiet=True)
            return harvested or reaped or code or lint_code or cadence_code
        if args.action == 'reap':
            return reap(root, entries, rh, dry_run=args.dry_run)
        if args.action == 'remove':
            remaining = [p for p in entries if p['name'] != args.name]
            if len(remaining) == len(entries):
                raise ValueError('unknown pod: ' + args.name)
            save_registry(root, remaining)
            print('unregistered ' + args.name)
            return 0
        if args.action == 'set':
            previous = next((p for p in entries if p['name'] == args.name), None)
            if previous is None:
                raise ValueError('unknown pod: ' + args.name)
            updated = dict(previous, work_queue=work_queue(args.work_queue))
            if done_file_path(updated):
                harvest.validate(updated, required=True)
            if updated != previous:
                # Preserve every row/field and retain a pre-edit backup. The
                # journal is append-only; an intent is not a committed edit.
                path = root / 'pods.json'
                saved = Path(str(path) + '.bak-ownership')
                if saved.exists():
                    saved = Path(str(saved) + '-' + uuid.uuid4().hex)
                shutil.copy2(path, saved)
                edit = dict(utc=rh.now(), pod=previous['name'], pod_id=previous['pod_id'],
                            field='work_queue', before=previous.get('work_queue'),
                            after=updated['work_queue'], backup=str(saved), edit_id=uuid.uuid4().hex)
                harvest.append(root / 'POD_REGISTRY.jsonl', dict(edit, event='SET_INTENT'))
                save_registry(root, [updated if p['name'] == args.name else p for p in entries])
                harvest.append(root / 'POD_REGISTRY.jsonl', dict(edit, event='SET_COMMITTED'))
            print(json.dumps(updated))
            return 0
        candidate = dict(name=args.name, pod_id=args.pod_id, ssh_target=args.ssh_target, lane=args.lane,
                         dollars_per_hour=args.dollars_per_hour, owner_note=args.owner_note, vcpus=1, gpus=0,
                         results=args.results, harvest_dest=args.harvest_dest)
        candidate.update(keep=args.keep, work_queue=args.work_queue)
        if args.ends_utc:
            candidate['ends_utc'] = args.ends_utc
        harvest.validate(candidate, required=True)
        # Validate before making any network connection, without writing registry.
        if (not re.fullmatch(NAME, args.name) or not re.fullmatch(r'[a-z0-9]+', args.pod_id)
                or not re.fullmatch(r'[a-z0-9-]+@ssh\.runpod\.io', args.ssh_target)
                or not math.isfinite(args.dollars_per_hour) or args.dollars_per_hour < 0
                or not args.owner_note.strip()):
            raise ValueError('invalid pod registration fields')
        if not (root / 'lanes' / args.lane).is_dir():
            raise ValueError('lane directory missing: ' + args.lane)
        if any(p['name'] == args.name or p['pod_id'] == args.pod_id or p['ssh_target'] == args.ssh_target for p in entries):
            raise ValueError('pod already registered; remove it before replacing')
        measured = probe(candidate)
        candidate.update(vcpus=measured['vcpus'], gpus=measured['gpus'])
        save_registry(root, entries + [candidate])
        print(json.dumps(candidate))
        return 0

# Everything below is independent of lane implementations and provider APIs.
# Deploy this module plus pod/runner.py; controller owns the queue, pod owns processes.
QUEUE = 'POD_QUEUE.json'
CLAIMS = 'POD_CLAIMS.json'
RECEIPTS = 'POD_PLAN_RECEIPTS.json'


def atomic_json(path, value):
    path = Path(path)
    backup(path)
    temporary = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    try:
        with temporary.open('w') as f:
            json.dump(value, f, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()  # this invocation's temporary only


def load_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default


@contextlib.contextmanager
def work_lock(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.pod-work.lock').open('a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def target_cores(target):
    cores = target.get('cores', 4)
    if type(cores) is not int or cores <= 0:
        raise ValueError('target cores must be a positive integer')
    return cores


def validate_target(target):
    if (not isinstance(target, dict) or not isinstance(target.get('id'), str)
            or not re.fullmatch(NAME, target['id']) or target.get('lane') not in LANES):
        raise ValueError('target requires a valid id and supported lane')
    argv = target.get('argv')
    if (not isinstance(argv, list) or not argv
            or any(not isinstance(a, str) or '\0' in a for a in argv) or not argv[0]):
        raise ValueError('target argv must be a nonempty JSON array of strings')
    target_cores(target)
    for key in ('gpu_bound', 'runnable'):
        if key in target and type(target[key]) is not bool:
            raise ValueError(key + ' must be boolean')
    deps = target.get('depends_on', [])
    if not isinstance(deps, list) or any(not isinstance(d, str) or not re.fullmatch(NAME, d) for d in deps):
        raise ValueError('depends_on must be target ids')


def queue_state(root):
    rows = load_json(root / QUEUE, [])
    claims = load_json(root / CLAIMS, {})
    if not isinstance(rows, list) or not isinstance(claims, dict):
        raise ValueError('invalid pod queue/claims shape')
    ids = set()
    for row in rows:
        validate_target(row)
        if row['id'] in ids:
            raise ValueError('duplicate target id')
        ids.add(row['id'])
    return rows, claims


def enqueue(root, target):
    validate_target(target)
    with work_lock(root):
        rows, _ = queue_state(root)
        if any(r['id'] == target['id'] for r in rows):
            raise ValueError('target id already exists; use a new attempt id for retries')
        atomic_json(root / QUEUE, rows + [target])


def runnable(rows, claims, lane, gpu):
    return [r for r in rows if r['lane'] == lane and r.get('runnable', False)
            and not r.get('owner') and r['id'] not in claims
            and r.get('gpu_bound', False) == gpu
            and all(claims.get(d, {}).get('status') == 'done' for d in r.get('depends_on', []))]


def slot_count(vcpus, cores=4, gpus=0, gpu=False):
    if (type(vcpus) not in (int, float) or not math.isfinite(vcpus) or vcpus <= 0
            or type(cores) is not int or cores <= 0 or type(gpus) is not int or gpus < 0):
        raise ValueError('invalid resource shape')
    return gpus if gpu else int(vcpus // cores)


def pack_cpu(candidates, capacity):
    # Integer-core subset packing avoids fragmentation from a large first target.
    choices = {0: []}
    limit = int(capacity)
    for row in candidates:
        cores = target_cores(row)
        for used, selected in list(choices.items()):
            if used + cores <= limit and used + cores not in choices:
                choices[used + cores] = selected + [row]
        if limit in choices:
            break
    return choices[max(choices)]


def plan_snapshot(rows, claims, lane, vcpus, gpus):
    slots = slot_count(vcpus, gpus=gpus, gpu=bool(gpus))
    candidates = runnable(rows, claims, lane, bool(gpus))
    if gpus:
        packed = candidates[:slots]
        fraction = len(packed) / slots
        math_text = '{} GPU slots (1/GPU), {} runnable GPU targets'.format(slots, len(candidates))
    else:
        # Mixed core declarations are packed by cores, never treated as four-core jobs.
        packed = pack_cpu(candidates, vcpus)
        occupied = sum(target_cores(r) for r in packed)
        # Slot fill excludes the unusable remainder of each declared core shape.
        # For uniform four-core targets this is exactly target_count / floor(vCPUs/4).
        fraction = min(1.0, sum(1 / slot_count(vcpus, target_cores(r)) for r in packed))
        math_text = ('floor({:g}/4)={} default slots; declared cores {}; '
                     '{} runnable CPU targets; {} / {:g} cores packed').format(
                         vcpus, slots, [target_cores(r) for r in packed], len(candidates), occupied, vcpus)
    passed = bool(packed) and fraction >= .60
    # For CPU shapes below four cores an explicitly smaller target may fill them.
    return dict(pass_=passed, slots=slots, fraction=fraction, targets=[r['id'] for r in packed],
                message='{} {}: {}; {:.1%} fill (need >=60%)'.format(
                    'PASS' if passed else 'FAIL', lane, math_text, fraction))


def queue_fingerprint(rows, claims):
    return hashlib.sha256(json.dumps([rows, claims], sort_keys=True).encode()).hexdigest()


def plan(root, lane, vcpus, gpus=0, now=None):
    if lane not in LANES:
        raise ValueError('unsupported lane')
    slot_count(vcpus, gpus=gpus)
    now = time.time() if now is None else now
    with work_lock(root):
        rows, claims = queue_state(root)
        result = plan_snapshot(rows, claims, lane, vcpus, gpus)
        result['pass'] = result.pop('pass_')
        receipts = load_json(root / RECEIPTS, [])
        # Invalidate an earlier PASS for this shape even when this check fails.
        receipts = [r for r in receipts if 0 <= now - r['time'] <= 1800
                    and (r['lane'], r['vcpus'], r['gpus']) != (lane, vcpus, gpus)]
        if result['pass']:
            receipts.append(dict(lane=lane, vcpus=vcpus, gpus=gpus, time=now, status='PASS',
                                 fingerprint=queue_fingerprint(rows, claims), targets=result['targets']))
        atomic_json(root / RECEIPTS, receipts)
        return result


def receipt_matches(root, lane, vcpus, gpus, now=None):
    now = time.time() if now is None else now
    with work_lock(root):
        rows, claims = queue_state(root)
        fingerprint = queue_fingerprint(rows, claims)
        receipts = load_json(root / RECEIPTS, [])
        return any(r.get('status') == 'PASS' and r.get('lane') == lane
                   and r.get('vcpus') == vcpus and r.get('gpus') == gpus
                   and 0 <= now - r['time'] <= 1800 and r.get('fingerprint') == fingerprint
                   for r in receipts)


class WorkQueue:
    """controller-only, process-safe ownership. Never expire a claim on a network failure."""
    def __init__(self, root, lane, owner, vcpus, gpus):
        if lane not in LANES or not owner:
            raise ValueError('lane and owner required')
        slot_count(vcpus, gpus=gpus)
        self.root, self.lane, self.owner = Path(root), lane, owner
        self.vcpus, self.gpus = vcpus, gpus

    def claim(self, free_cores, free_gpus):
        if not 0 <= free_cores <= self.vcpus or not 0 <= free_gpus <= self.gpus:
            raise ValueError('runner capacity disagrees with registry')
        with work_lock(self.root):
            rows, claims = queue_state(self.root)
            choices = runnable(rows, claims, self.lane, bool(self.gpus))
            packed = choices[:int(free_gpus)] if self.gpus else pack_cpu(choices, free_cores)
            target = packed[0] if packed else None
            if target is None:
                return None
            token = uuid.uuid4().hex
            claims[target['id']] = dict(owner=self.owner, token=token, status='claimed', time=time.time())
            atomic_json(self.root / CLAIMS, claims)
            return dict(target, claim_token=token)

    def finish(self, target, code):
        with work_lock(self.root):
            _, claims = queue_state(self.root)
            row = claims.get(target['id'], {})
            if row.get('owner') != self.owner or row.get('token') != target.get('claim_token'):
                raise ValueError('claim owner/token mismatch')
            row.update(status='done' if code == 0 else 'failed', returncode=code)
            atomic_json(self.root / CLAIMS, claims)


REAP_GRACE = 20 * 60
REAP_IDLE_GRACE = 60 * 60
REAP_IDLE_REPEAT = 6 * 60 * 60
REAP_MAX_GAP = 16 * 60  # one 15-minute scheduler interval plus probe jitter
REAP_EXCLUDED = settings.names('RH_REAP_EXCLUDED')


def reap_scope(pod):
    return hashlib.sha256(json.dumps(pod, sort_keys=True).encode()).hexdigest()


def done_file_snapshot(done, roots, now_ns=None):
    """Read-only pod-side evidence. Errors, missing roots and links fail closed."""
    import hashlib
    import json
    import os
    import stat
    import time
    now_ns = time.time_ns() if now_ns is None else now_ns
    cutoff = now_ns - 20 * 60 * 1_000_000_000

    def regular(path, directory=False):
        # Do not follow links in either the root or any parent component.
        parent = os.path.abspath(path)
        while parent != '/':
            if stat.S_ISLNK(os.lstat(parent).st_mode):
                raise ValueError('symlink in evidence path')
            parent = os.path.dirname(parent)
        info = os.lstat(path)
        if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
            raise ValueError('unsupported evidence file')
        return info

    try:
        marker = regular(done)
    except FileNotFoundError:
        return dict(exists=False, empty=False, generation='missing')
    signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    files, newest = [], None
    def failed(error):
        raise error
    if not roots:
        raise ValueError('missing results declaration')
    for root in roots:
        regular(root, directory=True)
        for directory, dirs, names in os.walk(root, followlinks=False, onerror=failed):
            for name in dirs:
                regular(os.path.join(directory, name), directory=True)
            for name in names:
                path = os.path.join(directory, name)
                info = regular(path)
                files.append((path, signature(info)))
                if max(info.st_mtime_ns, info.st_ctime_ns) >= cutoff:
                    newest = info.st_mtime_ns if newest is None else max(newest, info.st_mtime_ns)
    # A replacement/removal during the scan is not a valid observation.
    if signature(regular(done)) != signature(marker):
        raise ValueError('done file changed during scan')
    generation = hashlib.sha256(json.dumps((signature(marker), sorted(files))).encode()).hexdigest()
    return dict(exists=True, empty=newest is None or marker.st_mtime_ns > newest,
                generation=generation)


def done_file_command(pod):
    source = (inspect.getsource(done_file_snapshot)
              + '\nimport json; print(json.dumps(done_file_snapshot('
              + repr(done_file_path(pod)) + ', ' + repr(pod['results']) + ')))')
    # Compression keeps this well below the proxy PTY's input line limit.
    encoded = base64.b64encode(zlib.compress(source.encode())).decode()
    program = 'import base64,zlib; exec(zlib.decompress(base64.b64decode(' + repr(encoded) + ')))'
    return 'python3 -B -c ' + shlex.quote(program)


def reap_queue(root, pod, measured=None):
    """Harness scheduling is locked; done-file evidence also rechecks the lane's promise."""
    if done_file_path(pod):
        evidence = (probe(pod) if measured is None else measured).get('done_file')
        if (not isinstance(evidence, dict) or type(evidence.get('exists')) is not bool
                or type(evidence.get('empty')) is not bool
                or not isinstance(evidence.get('generation'), str) or not evidence['generation']
                or evidence['empty'] and not evidence['exists']):
            raise ValueError('done-file evidence unavailable')
        return dict(queued=0 if evidence['empty'] else 1, claimed=0,
                    work_generation=evidence['generation'])
    if pod.get('work_queue') != 'harness' or not (root / QUEUE).is_file():
        raise ValueError('queue ownership unknown')
    rows, claims = queue_state(root)
    if any(not isinstance(c, dict) or c.get('status') not in ('claimed', 'done', 'failed')
           for c in claims.values()):
        raise ValueError('invalid claim state')
    # Include blocked/owned/unrunnable items: no process is not proof of no work.
    relevant = [r for r in rows if r['lane'] == pod['lane']
                and r.get('gpu_bound', False) == bool(pod['gpus'])]
    queued = sum(claims.get(r['id'], {}).get('status') not in ('done', 'failed') for r in relevant)
    claimed = sum(c.get('owner') == pod['pod_id'] and c['status'] == 'claimed'
                  for c in claims.values())
    target_ids = {r['id'] for r in relevant}
    relevant_claims = {k: c for k, c in claims.items() if k in target_ids or c.get('owner') == pod['pod_id']}
    return dict(queued=queued, claimed=claimed,
                work_generation=queue_fingerprint(relevant, relevant_claims))


def reap_low(row, pod):
    if (row.get('reachable') is not True or row.get('vcpus') != pod['vcpus']
            or row.get('gpus') != pod['gpus']):
        return False
    cpu, gpu = row.get('cpu_frac'), row.get('gpu_util')
    if pod['gpus']:
        return type(gpu) in (int, float) and gpu == 0
    return type(cpu) in (int, float) and math.isfinite(cpu) and 0 <= cpu < .10


def reap_ready(history, pod, now, idle_only=False):
    recent = sorted((r for r in history if r.get('scope') == reap_scope(pod)
                     and harvest.pod_identity(r, pod) and not r.get('event')),
                    key=lambda r: stamp(r['utc']))
    end = stamp(now)
    if not recent or not 0 <= (end - stamp(recent[-1]['utc'])).total_seconds() <= 60:
        return False
    last = end
    generation = recent[-1].get('work_generation')
    for row in reversed(recent):
        when = stamp(row['utc'])
        if (not 0 <= (last - when).total_seconds() <= REAP_MAX_GAP
                or not reap_low(row, pod)
                or not idle_only and (not generation or row.get('work_generation') != generation
                    or any(row.get(k) != 0 for k in ('queued', 'claimed', 'running', 'pending')))):
            return False
        if (end - when).total_seconds() >= (REAP_IDLE_GRACE if idle_only else REAP_GRACE):
            return True
        last = when
    return False


def reap_mode(root, now, dry_run=False):
    config_path = settings.path('RH_POD_GATE_CONFIG', root / 'pod-gate.json')
    rollout = root / 'POD_REAP_ROLLOUT.json'
    if not rollout.exists():
        atomic_json(rollout, dict(started_utc=now))
    try:
        config = load_json(config_path, {})
        mode = config.get('reap')
        if mode not in (None, 'dry-run', 'enforce'):
            return 'dry-run'
        age = (stamp(now) - stamp(load_json(rollout, {})['started_utc'])).total_seconds()
        if dry_run or mode == 'dry-run':
            return 'dry-run'
        return 'enforce' if mode == 'enforce' or age >= 86400 else 'dry-run'
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return 'dry-run'


def reap_alert(root, pod, kind, now=None):
    """Durable alert and push contain names only, never paths, commands or credentials."""
    path = root / 'alerts' / ('pod-' + kind + '-' + pod['name'])
    path.parent.mkdir(parents=True, exist_ok=True)
    sent = path.with_name(path.name + '.notified')
    sent_value = pod['pod_id']
    write_alert = True
    if kind == 'idle':
        # Throttle durable writes to 6h, while retrying failed pushes without
        # rewriting the names-only alert file. Each window has its own receipt.
        throttle = path.with_name(path.name + '.throttle')
        try:
            previous_idle = load_json(throttle, {})
            same_window = (previous_idle.get('pod_id') == pod['pod_id']
                and (stamp(now) - stamp(previous_idle['utc'])).total_seconds() < REAP_IDLE_REPEAT)
        except (ValueError, KeyError, TypeError, AttributeError):
            same_window = False
        if same_window:
            write_alert = False
            alert_utc = previous_idle['utc']
        else:
            alert_utc = now
            atomic_json(throttle, dict(pod_id=pod['pod_id'], utc=now))
        sent_value += ':' + alert_utc
    previous = path.read_text() if path.exists() else None
    if previous == pod['name'] + '\n' and sent.exists() and sent.read_text() == sent_value:
        return
    if write_alert or previous != pod['name'] + '\n':
        with path.open('w') as alert_file:
            alert_file.write(pod['name'] + '\n')
            alert_file.flush()
            os.fsync(alert_file.fileno())
    try:
        result = subprocess.run([*settings.command('RH_NOTIFY_COMMAND', 'notify-agent'),
                                 pod['name'], 'Pod ' + kind],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        if result.returncode == 0:
            sent.write_text(sent_value)
    except (OSError, subprocess.SubprocessError):
        pass


def retry_reaped_alerts(root):
    # Terminated pods are no longer in the registry. Their durable completion
    # events must still drive retries if the push service was unavailable.
    latest = {}
    for row in harvest.rows(root / 'POD_REAP.jsonl'):
        if (row.get('event') == 'REAPED' and re.fullmatch(NAME, row.get('pod', ''))
                and re.fullmatch(r'[a-z0-9]+', row.get('pod_id', ''))):
            latest[row['pod']] = row
    for name, row in latest.items():
        reap_alert(root, dict(name=name, pod_id=row['pod_id']), 'reaped')


def reap_observe(root, pod, rh, idle_only=False, measured=None):
    row = dict(utc=rh.now(), pod=pod['name'], pod_id=pod['pod_id'], lane=pod['lane'],
               scope=reap_scope(pod), reachable=False)
    try:
        if not idle_only and not done_file_path(pod):
            row.update(reap_queue(root, pod))
        if measured is None:
            row.update(probe(pod))
        else:
            row.update({k: measured.get(k) for k in ('vcpus', 'gpus', 'cpu_frac', 'gpu_util')})
            if measured.get('reachable') is not True:
                raise ValueError('monitoring probe unavailable')
        if not idle_only:
            if done_file_path(pod):
                row.update(reap_queue(root, pod, measured=row))
            slots = dispatch(root, pod, None, mode='inspect')
            for field in ('running', 'pending'):
                if type(slots.get(field)) is not int or slots[field] < 0:
                    raise ValueError('invalid slot status')
                row[field] = slots[field]
        row['reachable'] = True
    except (OSError, ValueError, KeyError, TypeError, IndexError, EOFError, subprocess.SubprocessError):
        row['reachable'] = False
    row['utc'] = rh.now()
    harvest.append(root / 'POD_REAP.jsonl', row)
    return row


def reap(root, entries, rh, dry_run=False, quiet=False):
    mode = reap_mode(root, rh.now(), dry_run)
    retry_reaped_alerts(root)
    failed = False
    for pod in entries:
        if pod['name'] in REAP_EXCLUDED or pod['lane'] in REAP_EXCLUDED or pod.get('keep'):
            reason = 'protected'
        elif pod.get('work_queue') != 'harness' and not done_file_path(pod):
            # Unknown ownership can only alert. It never enters harvest/shutdown
            # or attaches to a runner; utilization probes are read-only.
            measured = None
            if quiet:
                # `check` just measured these pods under the same registry lock.
                # Reuse only its fresh, exact-identity observation; otherwise probe.
                monitoring = samples(harvest.rows(root / 'PODS.jsonl'), pod)
                if monitoring:
                    latest = monitoring[-1]
                    if (latest.get('pod_id') == pod['pod_id'] and
                            0 <= (stamp(rh.now()) - stamp(latest['utc'])).total_seconds() <= 60):
                        measured = latest
            row = reap_observe(root, pod, rh, idle_only=True, measured=measured)
            if reap_ready(harvest.rows(root / 'POD_REAP.jsonl'), pod, rh.now(), idle_only=True):
                reap_alert(root, pod, 'idle', now=rh.now())
            reason = 'queue ownership unknown'
        else:
            reason = None
        if reason:
            if not quiet:
                print(json.dumps(dict(pod=pod['name'], event='REAP_SKIP', reason=reason, mode=mode)))
            continue
        # Serialize scheduling for the authoritative observation, copy and shutdown.
        with work_lock(root):
            row = reap_observe(root, pod, rh)
            if not row['reachable']:
                reap_alert(root, pod, 'reap-unknown')
                failed = True
                continue
            if (pod.get('work_queue') == 'harness' and (row['queued'] or row['claimed'])
                    and row['running'] == 0 and row['pending'] == 0):
                reap_alert(root, pod, 'stuck')
                continue
            if not reap_ready(harvest.rows(root / 'POD_REAP.jsonl'), pod, rh.now()):
                continue
            reason = 'reap-final-' + uuid.uuid4().hex
            try:
                # Same implementation as `rh pods harvest NAME`; it fsyncs the run row
                # in finally. Check that exact new row on disk before any shutdown.
                started = stamp(rh.now())
                final = harvest.harvest_one(root, pod, rh, reason=reason)
                logged = harvest.rows(harvest.state_root(root) / 'HARVEST.jsonl')
                if (final.get('verified') is not True or not harvest.identity(final, pod)
                        or final.get('reason') != reason or final.get('event')
                        or not started <= stamp(final['utc']) <= stamp(rh.now())
                        or final not in logged):
                    raise ValueError('final harvest not durably verified')
                # Long harvests may overlap new work; recheck immediately before copy.
                fresh = reap_observe(root, pod, rh)
                if (not reap_low(fresh, pod)
                        or fresh.get('work_generation') != row.get('work_generation')
                        or any(fresh.get(k) != 0 for k in ('queued', 'claimed', 'running', 'pending'))):
                    continue
                current = next((p for p in registry(root) if p['pod_id'] == pod['pod_id']), None)
                if current != pod:  # includes a keep flag added during a long harvest
                    continue
                event = dict(fresh, event='WOULD_REAP' if mode == 'dry-run' else 'REAP_STARTED', mode=mode)
                harvest.append(root / 'POD_REAP.jsonl', event)
                print(json.dumps(event))
                if mode == 'dry-run':
                    continue
                # Existing full-workspace copy/hash handshake and pod-local terminate_self.
                # No controller-side API call or alternate termination command is introduced.
                destination = Path(pod['harvest_dest']) / pod['name'] / 'shutdown'
                shutdown_args = dict(mode='reap')
                if done_file_path(pod):
                    shutdown_args['expected_generation'] = fresh['work_generation']
                if dispatch(root, pod, destination, **shutdown_args) != 0:
                    raise ValueError('shutdown not acknowledged')
                harvest.append(root / 'POD_REAP.jsonl', dict(event, event='REAPED', utc=rh.now()))
                current = registry(root)
                backup_path = Path(str(root / 'pods.json') + '.bak-reap')
                if not backup_path.exists():
                    shutil.copy2(root / 'pods.json', backup_path)
                save_registry(root, [p for p in current if p['pod_id'] != pod['pod_id']])
                reap_alert(root, pod, 'reaped')
            except Exception:
                # Never print remote errors, banners, paths or credentials.
                reap_alert(root, pod, 'reap-failed')
                harvest.append(root / 'POD_REAP.jsonl', dict(row, event='REAP_FAILED', utc=rh.now()))
                failed = True
    return int(failed)


def workspace_manifest(workspace):
    """Regular files + directories, strict byte counts and SHA256; fail on links/special files."""
    workspace = Path(workspace)
    files, directories = {}, []
    for path in sorted(workspace.rglob('*')):
        rel = path.relative_to(workspace).as_posix()
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            directories.append(rel)
        elif stat.S_ISREG(mode):
            h = hashlib.sha256()
            count = 0
            with path.open('rb') as f:
                for block in iter(lambda: f.read(1024 * 1024), b''):
                    count += len(block)
                    h.update(block)
            files[rel] = dict(bytes=count, sha256=h.hexdigest())
        else:
            raise ValueError('workspace contains symlink or special file: ' + rel)
    return dict(files=files, directories=directories, bytes=sum(f['bytes'] for f in files.values()))


def safe_result_path(workspace, rel):
    path = Path(workspace) / rel
    if not rel or Path(rel).is_absolute() or '..' in Path(rel).parts:
        raise ValueError('unsafe result path')
    if not path.resolve().is_relative_to(Path(workspace).resolve()):
        raise ValueError('result escapes workspace')
    return path


def copy_results(channel, destination, manifest):
    """controller-side verified copy, chunked over the same SSH connection; no rsync/port needed."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)  # unique snapshot; never overwrite results
    for rel in manifest['directories']:
        safe_result_path(destination, rel).mkdir(parents=True, exist_ok=True)
    for rel, expected in manifest['files'].items():
        out = safe_result_path(destination, rel)
        out.parent.mkdir(parents=True, exist_ok=True)
        offset = 0
        with out.open('xb') as f:
            while offset < expected['bytes']:
                channel.send(dict(op='read', path=rel, offset=offset,
                                  size=min(65536, expected['bytes'] - offset)))
                reply = channel.receive()
                if reply.get('op') != 'data':
                    raise ValueError('result read failed')
                data = base64.b64decode(reply['data'], validate=True)
                if not data or len(data) > expected['bytes'] - offset:
                    raise ValueError('invalid result byte count')
                f.write(data)
                offset += len(data)
            f.flush()
            os.fsync(f.fileno())
    actual = workspace_manifest(destination)
    if actual != manifest:
        raise ValueError('result byte count or SHA256 verification failed')
    return actual


class JsonChannel:
    def __init__(self, reader, writer, deadline=None):
        self.reader, self.writer = reader, writer
        self.deadline, self.buffer = deadline, b''

    def send(self, value):
        data = memoryview((json.dumps(value) + '\n').encode())
        while data:
            written = self.writer.write(data)
            if not written:
                raise EOFError('controller write failed')
            data = data[written:]
        self.writer.flush()

    def receive(self):
        if self.deadline is None:
            line = self.reader.readline(4 * 1024 * 1024)
        else:
            while b'\n' not in self.buffer:
                left = self.deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError('runner inspection/shutdown timed out')
                if select.select([self.reader], [], [], min(left, .25))[0]:
                    data = os.read(self.reader.fileno(), 65536)
                    if not data:
                        raise EOFError('runner disconnected')
                    self.buffer += data
                    if len(self.buffer) > 4 * 1024 * 1024:
                        raise ValueError('runner message limit')
            line, self.buffer = self.buffer.split(b'\n', 1)
        if not line:
            raise EOFError('controller disconnected')
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError('invalid protocol message')
        return value


class PodQueue:
    """On-pod pull RPC over a controller-initiated local Unix socket attachment."""
    def __init__(self, channel, workspace):
        self.channel, self.workspace = channel, Path(workspace)

    def rpc(self, op, **fields):
        self.channel.send(dict(op=op, **fields))
        reply = self.channel.receive()
        if reply.get('error'):
            raise ValueError(reply['error'])
        return reply

    def claim(self, free_cores, free_gpus):
        return self.rpc('claim', free_cores=free_cores, free_gpus=free_gpus).get('target')

    def finish(self, target, code):
        self.rpc('finish', target=target, code=code)

    def copy(self):
        before = workspace_manifest(self.workspace)
        self.channel.send(dict(op='copy', manifest=before))
        while True:
            req = self.channel.receive()
            if req.get('op') == 'copied':
                if req.get('error') or req.get('manifest') != before:
                    raise ValueError('controller did not verify the copy')
                if workspace_manifest(self.workspace) != before:
                    raise ValueError('workspace changed during copy')
                return before
            if req.get('op') != 'read':
                raise ValueError('invalid copy protocol')
            path = safe_result_path(self.workspace, req['path'])
            if req['path'] not in before['files'] or not 0 < req['size'] <= 65536:
                raise ValueError('invalid result read')
            with path.open('rb') as f:
                f.seek(req['offset'])
                data = f.read(req['size'])
            self.channel.send(dict(op='data', data=base64.b64encode(data).decode()))

    def alert(self, message):
        self.rpc('alert', message=message)


class SlotRunner:
    """Resource accounting, immediate refill, and copy-before-terminate on the pod."""
    def __init__(self, workspace, vcpus, gpus=0, terminate=None, clock=time.monotonic,
                 launch=None, idle_seconds=1200, auto_shutdown=True):
        slot_count(vcpus, gpus=gpus)
        if idle_seconds < 1200:
            raise ValueError('idle grace must be at least 20 minutes')
        self.workspace, self.vcpus, self.gpus = Path(workspace), vcpus, gpus
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.terminate = terminate
        self.clock, self.launch, self.idle_seconds = clock, launch or self.start, idle_seconds
        self.active, self.completed, self.seen = {}, [], set()
        self.idle_since, self.ended = None, False
        self.auto_shutdown, self.draining = auto_shutdown, False
        self.copy_retry_at = 0

    def start(self, target, gpu):
        env = dict(os.environ)
        threads = str(target_cores(target))
        env.update(OMP_NUM_THREADS=threads, MKL_NUM_THREADS=threads, OPENBLAS_NUM_THREADS=threads,
                   RH_TARGET_ID=target['id'])
        env['CUDA_VISIBLE_DEVICES'] = str(gpu) if gpu is not None else ''
        # Logs are copied with all results; no output blocks the control channel.
        with (self.workspace / ('rh-' + target['id'] + '.log')).open('ab') as log:
            return subprocess.Popen(target['argv'], cwd=self.workspace, env=env,
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                    start_new_session=True)

    def poll(self):
        for token, (target, process, gpu) in list(self.active.items()):
            code = process.poll()
            if code is not None:
                self.completed.append((target, code))
                del self.active[token]

    def tick(self, queue):
        if self.ended or self.draining:
            return
        self.poll()
        while self.completed:
            target, code = self.completed[0]
            queue.finish(target, code)
            self.completed.pop(0)
        while True:
            used = sum(target_cores(t) for t, _, _ in self.active.values())
            free_cores = max(0, self.vcpus - used)
            gpu_ids = {gpu for _, _, gpu in self.active.values()}
            free_gpus = self.gpus - len(self.active) if self.gpus else 0
            if (self.gpus and free_gpus <= 0) or (not self.gpus and free_cores <= 0):
                break
            target = queue.claim(free_cores, free_gpus)
            if target is None:
                break
            validate_target(target)
            token = target.get('claim_token')
            if not token or token in self.seen or target.get('gpu_bound', False) != bool(self.gpus):
                raise ValueError('invalid or repeated claim')
            if not self.gpus and target_cores(target) > free_cores:
                raise ValueError('claim exceeds free capacity')
            self.seen.add(token)
            gpu = next((i for i in range(self.gpus) if i not in gpu_ids), None)
            self.idle_since = None
            try:
                process = self.launch(target, gpu)
            except OSError:
                self.completed.append((target, 127))
                continue
            self.active[token] = (target, process, gpu)
        if self.active or self.completed:
            self.idle_since = None
            return
        now = self.clock()
        if self.idle_since is None:
            self.idle_since = now
        if (not self.auto_shutdown or now - self.idle_since < self.idle_seconds
                or now < self.copy_retry_at):
            return
        self.verified_shutdown(queue)

    def verified_shutdown(self, queue):
        """Shared shutdown path; both idle runner and controller reaper must verify full copy."""
        self.poll()
        if self.active or self.completed:
            raise ValueError('runner still has work or unreported completions')
        try:
            queue.copy()
            if self.terminate is None:
                raise ValueError('no terminate command configured')
            # This callback runs ONLY on the pod (or is a fake in local simulation).
            self.terminate()
            self.ended = True
        except (OSError, ValueError, KeyError, EOFError, subprocess.SubprocessError):
            self.copy_retry_at = self.clock() + 60
            queue.alert('Pod result copy/termination failed; retaining workspace and pod')


def notify_copy_failure(pod, message):
    try:
        result = subprocess.run([*settings.command('RH_NOTIFY_COMMAND', 'notify-agent'),
                                 pod['name'] + ': ' + message, 'Pod results need attention'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def serve_controller(channel, queue, destination, pod, notify=notify_copy_failure, mode='dispatch',
                     require_reaper=False, expected_generation=None):
    """controller request handler. No API key or lifecycle API is present here."""
    greeted, copy_verified = False, False
    done_generation = None
    while True:
        req = channel.receive()
        op = req.get('op')
        if mode != 'dispatch' and not greeted and op != 'hello':
            raise ValueError('runner identity must be checked first')
        if op == 'hello':
            if (req.get('vcpus'), req.get('gpus'), req.get('lane'), req.get('owner')) != (
                    queue.vcpus, queue.gpus, queue.lane, queue.owner):
                raise ValueError('runner identity/resources disagree with registry')
            if mode != 'dispatch':
                if 'reap-v1' not in req.get('capabilities', []):
                    channel.send(dict(ok=False))  # old daemon must never enter its work loop
                    raise ValueError('runner does not support safe inspection/reaping')
                if mode == 'reap' and any(req.get(k) != 0 for k in ('running', 'pending')):
                    channel.send(dict(ok=False))
                    raise ValueError('runner acquired work before shutdown')
                if require_reaper and mode == 'reap' and done_file_path(pod):
                    measured = dict(probe(pod), reachable=True)
                    state = reap_queue(queue.root, pod, measured=measured)
                    if (not reap_low(measured, pod) or state['queued'] or state['claimed']
                            or expected_generation is not None and state['work_generation'] != expected_generation):
                        channel.send(dict(ok=False))
                        raise ValueError('done-file authorization changed before copy')
                    done_generation = state['work_generation']
                channel.send(dict(ok=True, mode=mode))
                greeted = True
                if mode == 'inspect':
                    return {k: req[k] for k in ('running', 'pending')}
            else:
                channel.send(dict(ok=True))
        elif op == 'claim':
            if mode != 'dispatch':
                raise ValueError('claims forbidden during inspection/reaping')
            channel.send(dict(target=queue.claim(req['free_cores'], req['free_gpus'])))
        elif op == 'finish':
            queue.finish(req['target'], req['code'])
            channel.send(dict(ok=True))
        elif op == 'copy':
            if require_reaper and mode != 'reap':
                channel.send(dict(op='copied', error='shutdown requires rh pods reap authorization'))
                continue
            try:
                snapshot = Path(destination) / (pod['pod_id'] + '-' + uuid.uuid4().hex)
                copied = copy_results(channel, snapshot, req['manifest'])
                if require_reaper and mode == 'reap':
                    current = next((p for p in registry(queue.root) if p['pod_id'] == pod['pod_id']), None)
                    measured = dict(probe(pod), reachable=True) if done_file_path(pod) else None
                    state = reap_queue(queue.root, pod, measured=measured)
                    if (current != pod or pod.get('keep') or state['queued'] or state['claimed']
                            or done_file_path(pod) and (not reap_low(measured, pod)
                                or state['work_generation'] != done_generation)):
                        raise ValueError('shutdown authorization changed during copy')
                channel.send(dict(op='copied', manifest=copied))
                copy_verified = True
            except (OSError, ValueError, EOFError, KeyError):
                notify(pod, 'Result copy failed; pod must remain alive')
                channel.send(dict(op='copied', error='copy failed'))
        elif op == 'alert':
            if not notify(pod, req['message']):
                print('rh pods: push failed for ' + pod['name'], file=sys.stderr)
            channel.send(dict(ok=True))
            if mode == 'reap':
                raise ValueError('verified shutdown failed; pod retained')
        elif op == 'ended':
            if mode == 'reap' and (req.get('verified_shutdown') is not True or not copy_verified):
                raise ValueError('shutdown was not verified')
            return 0
        else:
            raise ValueError('unknown runner request')


def dispatch(root, pod, destination, mode='dispatch', expected_generation=None):
    """controller opens existing strict proxy SSH, then bridges a preinstalled runner socket."""
    if mode not in ('dispatch', 'inspect', 'reap'):
        raise ValueError('unknown runner mode')
    key = settings.path('RH_SSH_KEY', Path('.runtime/ssh/id_ed25519'))
    hosts = settings.path('RH_KNOWN_HOSTS', Path('.runtime/ssh/known_hosts'))
    argv = ['/usr/bin/ssh', '-tt', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=' + str(hosts),
            '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2',
            '-i', str(key), pod['ssh_target']]
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL)
    try:
        # Proxy supplies an interactive shell. Never send keys, API requests or secrets.
        data, deadline = b'', time.monotonic() + 40
        while b'# ' not in data:
            if time.monotonic() >= deadline:
                raise TimeoutError('proxy shell unavailable')
            if select.select([process.stdout], [], [], .25)[0]:
                block = os.read(process.stdout.fileno(), 4096)
                if not block:
                    raise EOFError('proxy closed')
                data += block
                if len(data) > 65536:
                    raise ValueError('proxy banner exceeds limit')
        nonce = uuid.uuid4().hex
        command = ("stty raw -echo; export PS1='' PS2=''; unset PROMPT_COMMAND; "
                   "printf '\\nRH_READY_" + nonce + "\\n'; "
                   'exec python3 ' + shlex.quote(os.environ.get('RH_REMOTE_RUNNER', 'research-harness/pod/runner.py')) + ' attach\n')
        process.stdin.write(command.encode())
        process.stdin.flush()
        marker = ('\nRH_READY_' + nonce + '\n').encode()
        data = b''
        while marker not in data:
            if time.monotonic() >= deadline:
                raise TimeoutError('proxy attach unavailable')
            if select.select([process.stdout], [], [], .25)[0]:
                block = os.read(process.stdout.fileno(), 1)  # do not consume protocol bytes
                if not block:
                    raise EOFError('proxy closed')
                data += block
        queue = WorkQueue(root, pod['lane'], pod['pod_id'], pod['vcpus'], pod['gpus'])
        deadline = time.monotonic() + (60 if mode == 'inspect' else 600) if mode != 'dispatch' else None
        return serve_controller(JsonChannel(process.stdout, process.stdin, deadline=deadline),
                                queue, destination, pod, mode=mode,
                                require_reaper=True, expected_generation=expected_generation,
                                notify=notify_copy_failure if mode == 'dispatch' else lambda *_: True)
    except (OSError, ValueError, EOFError):
        if mode == 'dispatch':
            notify_copy_failure(pod, 'Controller disconnected; verify results before ending pod')
        raise ValueError('pod dispatcher disconnected (claims retained)')
    finally:
        if process.poll() is None:
            process.kill()  # local SSH process only
        process.wait()
        process.stdin.close()
        process.stdout.close()


def runner_main(argv=None):
    parser = argparse.ArgumentParser(description='On-pod slot runner; never run serve on the controller')
    parser.add_argument('mode', choices=('serve', 'attach'))
    parser.add_argument('--socket', type=Path, default=settings.path('RH_RUNNER_SOCKET', Path('.runtime/rh-pod-runner.sock')))
    parser.add_argument('--workspace', type=Path, default=settings.path('RH_POD_WORKSPACE', Path('/workspace')))
    parser.add_argument('--vcpus', type=float)
    parser.add_argument('--gpus', type=int)
    parser.add_argument('--lane', choices=sorted(LANES))
    parser.add_argument('--owner')
    parser.add_argument('--terminate-json', help='immediate pod-local command, e.g. ["bash","/root/podend-now.sh"]')
    args = parser.parse_args(argv)
    if args.mode == 'attach':
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(args.socket))
            def upload():
                while True:
                    data = os.read(sys.stdin.fileno(), 65536)
                    if not data:
                        client.shutdown(socket.SHUT_WR)
                        return
                    client.sendall(data)
            threading.Thread(target=upload, daemon=True).start()
            while True:
                data = client.recv(65536)
                if not data:
                    return 0
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
    if not args.lane or not args.owner:
        parser.error('serve requires --lane and --owner')
    command = json.loads(args.terminate_json) if args.terminate_json else None
    if command is not None and (not isinstance(command, list) or not command
                                or any(not isinstance(a, str) for a in command)):
        parser.error('terminate-json must be a command array')
    if command is None and os.environ.get('RUNPOD_POD_ID') != args.owner:
        parser.error('default termination requires the pod own RUNPOD_POD_ID to match --owner')
    if command is None:
        measured_cpu, measured_gpu = local_resources()
        if ((args.vcpus is not None and args.vcpus != measured_cpu)
                or (args.gpus is not None and args.gpus != measured_gpu)):
            parser.error('declared resources disagree with actual pod allowance')
        args.vcpus, args.gpus = measured_cpu, measured_gpu
    elif args.vcpus is None or args.gpus is None:
        parser.error('custom terminate/simulation requires explicit --vcpus and --gpus')
    # A stale socket is deliberately not removed: an owner must inspect before restart.
    runner = SlotRunner(args.workspace, args.vcpus, args.gpus,
                        terminate=(lambda: subprocess.run(command, check=True, timeout=60))
                        if command else terminate_self, auto_shutdown=False)
    # A persistent work dispatcher must not block the scheduler's read-only
    # inspection/reap attachments. Only one connection may dispatch workers.
    dispatcher_lock, shutdown_lock = threading.Lock(), threading.Lock()
    shutdown_done = threading.Event()

    def poll_disconnected():
        if dispatcher_lock.acquire(blocking=False):
            try:
                runner.poll()
            finally:
                dispatcher_lock.release()

    def connection(conn):
        with conn:
            conn.settimeout(90)
            stream = conn.makefile('rwb', buffering=65536)
            channel = JsonChannel(stream, stream)
            queue = PodQueue(channel, args.workspace)
            owns_dispatcher, reaping = False, False
            try:
                poll_disconnected()
                channel.send(dict(op='hello', lane=args.lane, owner=args.owner,
                                  vcpus=runner.vcpus, gpus=runner.gpus,
                                  capabilities=['reap-v1'], running=len(runner.active),
                                  pending=len(runner.completed)))
                reply = channel.receive()
                if reply.get('ok') is not True:
                    raise ValueError('controller rejected runner identity')
                if reply.get('mode') == 'inspect':
                    return
                if reply.get('mode') == 'reap':
                    # controller holds work_lock: no dispatcher can acquire a new claim.
                    # An in-flight claim is visible in POD_CLAIMS and prevents reap.
                    with shutdown_lock:
                        reaping = runner.draining = True
                        runner.verified_shutdown(queue)
                        if runner.ended:
                            channel.send(dict(op='ended', verified_shutdown=True))
                    return
                owns_dispatcher = dispatcher_lock.acquire(blocking=False)
                if not owns_dispatcher:
                    raise ValueError('another work dispatcher is already connected')
                while not runner.ended:
                    runner.tick(queue)
                    time.sleep(1)
            except (OSError, EOFError, ValueError):
                if owns_dispatcher:
                    runner.idle_since = None  # disconnected controllers never prove exhaustion
            finally:
                if owns_dispatcher:
                    dispatcher_lock.release()
                if reaping:
                    if runner.ended:
                        shutdown_done.set()  # attempt acknowledgment before daemon exits
                    else:
                        runner.draining = False
                stream.close()

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(args.socket))
        os.chmod(args.socket, 0o600)
        server.listen(8)
        server.settimeout(1)
        while not shutdown_done.is_set():
            try:
                conn, _ = server.accept()
            except socket.timeout:
                poll_disconnected()  # reap local jobs even if controller is disconnected
                continue
            threading.Thread(target=connection, args=(conn,), daemon=True).start()
    return 0


def terminate_self():
    """On-pod only: the immediate podend.sh GraphQL mechanism, after verified copy.

    Never called by dispatch/check/plan or imported-module initialization.
    Credentials stay in the pod environment and never enter protocol messages.
    """
    import urllib.request
    pod_id = os.environ['RUNPOD_POD_ID']
    api_key = os.environ['RUNPOD_API_KEY']
    if not re.fullmatch(r'[a-z0-9]+', pod_id) or not api_key:
        raise ValueError('pod termination environment unavailable')
    query = 'mutation { podTerminate(input:{podId:' + json.dumps(pod_id) + '}) }'
    request = urllib.request.Request('https://api.runpod.io/graphql',
        data=json.dumps(dict(query=query)).encode(),
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + api_key})
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)
    if (result.get('errors') or 'podTerminate' not in (result.get('data') or {})
            or result['data']['podTerminate'] is False):
        raise ValueError('pod termination rejected')


def local_resources(cgroup=None, nproc=None, gpu_probe=None):
    """Pod-measured allowance, not driver concurrency; cgroup v1 and v2."""
    if nproc is None:
        nproc = len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else os.cpu_count()
    cgroup = cgroup if cgroup is not None else settings.path('RH_CGROUP_ROOT', Path('/sys/fs/cgroup'))
    cpu_max = cgroup / 'cpu.max'
    quota = cpu_max.read_text() if cpu_max.exists() else 'max 100000'
    if not cpu_max.exists():
        for directory in ('cpu', 'cpuacct', 'cpuacct,cpu', 'cpu,cpuacct'):
            base = cgroup / directory
            if (base / 'cpu.cfs_quota_us').exists():
                quota = (base / 'cpu.cfs_quota_us').read_text().strip() + ' ' + (
                    base / 'cpu.cfs_period_us').read_text().strip()
                break
    vcpus = allowance(str(nproc), quota)
    def devices():
        try:
            result = subprocess.run(['nvidia-smi', '-L'], capture_output=True, text=True, timeout=20)
            if result.returncode:
                raise ValueError('GPU inventory failed')
            return result.stdout
        except FileNotFoundError:
            return ''
    output = (gpu_probe or devices)()
    gpus = sum(bool(re.match(r'^GPU \d+:', line)) for line in output.splitlines())
    return vcpus, gpus
