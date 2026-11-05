"""Lossless, local-only hard-link consolidation of committed harvest payloads.

No manifest/index changes. Different modes/owners/ACLs/xattrs are incompatible:
those properties belong to an inode, not a directory entry. Birth time selects
an inode on macOS; inode number breaks ties. A durable pending receipt bridges
rename and the append-only journal, so a killed process can be reconciled.
"""
import collections
import contextlib
import ctypes
import sys
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import time
import uuid

import harvest

# These audited inodes have aliases outside the harvest roots. The general
# nlink reconciliation below also excludes newly discovered external aliases.
EXCLUDED_INODES = {82613675, 82942650}
JOURNAL = 'HARVEST_DEDUPE.jsonl'


def identity(st):
    return st.st_dev, st.st_ino


def stamp(st):
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns,
            stat.S_IMODE(st.st_mode), st.st_uid, st.st_gid)


def checked_hash(path, expected=None):
    """Hash a no-follow fd, checking both fd and directory entry afterwards."""
    harvest.local_path(path.parent, path.name)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or expected and stamp(before) != stamp(expected):
            raise ValueError('changed_since_scan')
        h = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
        if stamp(os.fstat(stream.fileno())) != stamp(before) or stamp(path.lstat()) != stamp(before):
            raise ValueError('changed_during_hash')
    return h.hexdigest(), before


def metadata(path, st):
    # Apple's system Python does not expose os.listxattr/getxattr. Use the
    # native no-follow API, and compare extended ACLs separately as well.
    if sys.platform == 'darwin':
        libc = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
        libc.listxattr.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
        libc.listxattr.restype = ctypes.c_ssize_t
        libc.getxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p,
                                 ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
        libc.getxattr.restype = ctypes.c_ssize_t
        encoded = os.fsencode(path)
        length = libc.listxattr(encoded, None, 0, 1)
        if length < 0:
            raise OSError(ctypes.get_errno(), 'listxattr failed')
        names = ctypes.create_string_buffer(length)
        if libc.listxattr(encoded, names, length, 1) != length:
            raise ValueError('metadata_changed')
        attrs = []
        for key in sorted(filter(None, names.raw.split(b'\0'))):
            size = libc.getxattr(encoded, key, None, 0, 0, 1)
            if size < 0:
                raise OSError(ctypes.get_errno(), 'getxattr failed')
            value = ctypes.create_string_buffer(size)
            if libc.getxattr(encoded, key, value, size, 0, 1) != size:
                raise ValueError('metadata_changed')
            attrs.append((key, value.raw))
        libc.acl_get_file.argtypes = [ctypes.c_char_p, ctypes.c_int]
        libc.acl_get_file.restype = ctypes.c_void_p
        libc.acl_to_text.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        libc.acl_to_text.restype = ctypes.c_void_p
        libc.acl_free.argtypes = [ctypes.c_void_p]
        acl = libc.acl_get_file(encoded, 0x100)  # ACL_TYPE_EXTENDED
        if not acl:
            if ctypes.get_errno() == 2 and path.exists():
                return (stat.S_IMODE(st.st_mode), st.st_uid, st.st_gid,
                        getattr(st, 'st_flags', 0), tuple(attrs), b'')
            raise OSError(ctypes.get_errno(), 'acl_get_file failed')
        text = None
        try:
            text = libc.acl_to_text(acl, None)
            if not text:
                raise OSError(ctypes.get_errno(), 'acl_to_text failed')
            acl_text = ctypes.string_at(text)
        finally:
            if text:
                libc.acl_free(text)
            libc.acl_free(acl)
    else:
        attrs = [(key, os.getxattr(path, key, follow_symlinks=False))
                 for key in sorted(os.listxattr(path, follow_symlinks=False))]
        acl_text = b''
    return (stat.S_IMODE(st.st_mode), st.st_uid, st.st_gid,
            getattr(st, 'st_flags', 0), tuple(attrs), acl_text)


def open_writers():
    """Use inode identities, so an open alias is protected too. Fail closed."""
    result = subprocess.run(['/usr/sbin/lsof', '-nP', '-F', 'faDi'],
                            capture_output=True, text=True, timeout=45)
    if result.returncode not in (0, 1) or (result.returncode == 1 and not result.stdout):
        raise RuntimeError('cannot inspect open writers')
    found, current = set(), {}
    def finish():
        if current.get('a') in ('w', 'u') and 'D' in current and 'i' in current:
            found.add((int(current['D'], 16), int(current['i'])))
    for line in result.stdout.splitlines():
        if line[:1] in ('p', 'f'):
            finish()
            current = {}
        elif line[:1] in ('a', 'D', 'i'):
            current[line[0]] = line[1:]
    finish()
    return found


def temporary(name):
    return (name == '.harvest.lock' or name.endswith(('.tmp', '.part', '.partial'))
            or name.startswith(('.harvest-dedupe-', '.harvest-')))


def journal_append(path, record):
    # Deduplicate replay after a crash between fsync(journal) and receipt cleanup.
    key = record['path'], record['old_inode'], record['canonical_inode']
    for old in harvest.rows(path):
        if (old.get('path'), old.get('old_inode'), old.get('canonical_inode')) == key:
            return
    harvest.append(path, record)


def recover(dest, allowed_bases):
    """Reconcile only our receipts; never unlink a pre-existing logical path."""
    for receipt in sorted(dest.glob('.harvest-dedupe-pending-*.json')):
        harvest.local_path(dest, receipt.name)
        journal = harvest.local_path(dest, JOURNAL)
        pending = json.loads(receipt.read_text())
        record = pending['record']
        path = harvest.local_path(dest, str(Path(record['path']).relative_to(dest)))
        temp = harvest.local_path(path.parent, pending['temp'])
        if not any(path.is_relative_to(base / 'snapshots') for base in allowed_bases):
            continue
        st = path.lstat()  # An absent logical path is an error, never fabricated.
        if st.st_ino == record['canonical_inode']:
            if checked_hash(path)[0] != record['sha256']:
                raise ValueError('pending replacement changed')
            journal_append(journal, record)
        elif st.st_ino != record['old_inode']:
            raise ValueError('pending replacement has unexpected inode')
        # These are pre-existing crash artifacts: cleanup is reversible.
        if temp.exists():
            harvest.trash_snapshot(temp)
        harvest.trash_snapshot(receipt)


def replace(path, canonical, original, canonical_stat, sha, journal, freed, canonical_links=None, original_links=None):
    """The only payload mutation: temp hard link, then same-directory rename."""
    if metadata(path, original) != metadata(canonical, canonical_stat):
        raise ValueError('incompatible_metadata')
    if checked_hash(canonical, canonical_stat)[0] != sha or checked_hash(path, original)[0] != sha:
        raise ValueError('changed_before_link')
    if stamp(path.lstat()) != stamp(original) or stamp(canonical.lstat()) != stamp(canonical_stat):
        raise ValueError('changed_before_link')
    if canonical_links is None:
        canonical_links = canonical.lstat().st_nlink
    if original_links is None:
        original_links = original.st_nlink
    if canonical.lstat().st_nlink != canonical_links or path.lstat().st_nlink != original_links:
        raise ValueError('changed_link_count')
    token = uuid.uuid4().hex
    temp = path.with_name('.harvest-dedupe-' + token + '.tmp')
    receipt = journal.parent / ('.harvest-dedupe-pending-' + token + '.json')
    record = dict(path=str(path), old_inode=original.st_ino,
                  canonical_inode=canonical_stat.st_ino, sha256=sha,
                  bytes_freed=freed, old_mtime_ns=original.st_mtime_ns)
    # The pending receipt is outside snapshots; it is not a journal event.
    harvest.atomic(receipt, dict(record=record, temp=temp.name))
    renamed = False
    try:
        os.link(canonical, temp, follow_symlinks=False)
        harvest.sync_dir(path.parent)
        if (stamp(path.lstat()) != stamp(original)
                or stamp(temp.lstat()) != stamp(canonical_stat)
                or stamp(canonical.lstat()) != stamp(canonical_stat)
                or canonical.lstat().st_nlink != canonical_links + 1
                or path.lstat().st_nlink != original_links):
            raise ValueError('changed_before_rename')
        os.replace(temp, path)
        renamed = True
        harvest.sync_dir(path.parent)
        harvest.append(journal, record)
        receipt.unlink()  # receipt created by this invocation
        harvest.sync_dir(journal.parent)
    finally:
        if temp.exists():
            temp.unlink()  # temp hard link created by this invocation
        # Keep a committed receipt until its journal append is durable.
        if not renamed and receipt.exists():
            receipt.unlink()
    return record


def run(entries, apply=False, name=None, new_snapshot=None, progress=None):
    """Manual: all children of registered destinations, including retired pods.

    Automatic: one registered pod/lane, and only its new snapshot is replaced.
    Lock files are persistent in this harness: flock ownership, not existence,
    means a harvest is in progress. Dry-run never creates a file or directory.
    """
    if name and not any(p['name'] == name for p in entries):
        raise ValueError('unknown pod: ' + name)
    destinations = sorted({Path(p['harvest_dest']) for p in entries if p.get('harvest_dest')})
    result = dict(apply=apply, replaced_paths=0, bytes_freed=0, candidate_paths=0,
                  candidate_bytes=0, skipped=collections.Counter(), touched_snapshots=[],
                  verification=[], df_before={}, df_after={})
    files, by_snapshot, manifests, metadata_before = {}, {}, {}, {}
    def emit(message):
        if progress:
            progress(message)
    with contextlib.ExitStack() as stack:
        bases = []
        for dest in destinations:
            harvest.local_path(dest.parent, dest.name)
            if not dest.is_dir():
                continue
            result['df_before'][str(dest)] = os.statvfs(dest).f_bavail * os.statvfs(dest).f_frsize
            # Dedupers and harvests serialize using the SAME per-pod lock.
            for base in sorted(dest.iterdir()):
                if base.is_symlink() or not base.is_dir() or name and base.name != name:
                    continue
                if new_snapshot and base != Path(new_snapshot).parent.parent:
                    continue
                snapshots = harvest.local_path(base, 'snapshots')
                if not snapshots.is_dir():
                    continue
                lock_path = harvest.local_path(base, '.harvest.lock')
                lock = stack.enter_context(lock_path.open('a' if apply else 'r')) if (apply or lock_path.exists()) else None
                busy = False
                if lock:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        busy = True
                        result['skipped']['locked_pod'] += 1
                bases.append((dest, base, snapshots, busy))
                if not busy:
                    index_path = harvest.local_path(base, 'INDEX.json')
                    if index_path.exists():
                        metadata_before[index_path] = index_path.read_bytes()
            # Recovery must run with all the acquired harvest locks. Pending
            # receipts from a currently busy pod are left for a later pass.
        if apply:
            for dest in destinations:
                if dest.is_dir() and not any(busy for d, _, _, busy in bases if d == dest):
                    recover(dest, [base for d, base, _, busy in bases if d == dest and not busy])
        aliases = collections.Counter()
        for dest, base, snapshots, busy in bases:
            for snapshot in sorted(snapshots.iterdir()):
                if snapshot.is_symlink() or not snapshot.is_dir():
                    continue
                paths, has_temp, unsafe = [], False, False
                for parent, dirs, names in os.walk(snapshot, followlinks=False):
                    for d in dirs[:]:
                        p = Path(parent) / d
                        if p.is_symlink():
                            unsafe = True
                            dirs.remove(d)
                        has_temp |= temporary(d)
                    for n in names:
                        p = Path(parent) / n
                        st = p.lstat()
                        if stat.S_ISREG(st.st_mode):
                            aliases[identity(st)] += 1
                            if p != snapshot / 'MANIFEST.json':
                                paths.append((p, st))
                        else:
                            unsafe = True
                        has_temp |= temporary(n)
                try:
                    manifest_path = harvest.local_path(base, str(snapshot.relative_to(base) / 'MANIFEST.json'))
                    manifest = json.loads(manifest_path.read_text())
                    if not isinstance(manifest['files_index'], dict):
                        raise ValueError('invalid manifest')
                    if manifest.get('pod') != base.name:
                        raise ValueError('manifest pod mismatch')
                    if new_snapshot and manifest.get('lane') != next(p['lane'] for p in entries if p['name'] == name):
                        raise ValueError('manifest lane mismatch')
                except (OSError, ValueError, KeyError, TypeError):
                    result['skipped']['unfinished_snapshot'] += 1
                    continue
                if busy or has_temp or unsafe:
                    result['skipped']['unsafe_or_in_progress_snapshot'] += 1
                    continue
                metadata_before[snapshot / 'MANIFEST.json'] = (snapshot / 'MANIFEST.json').read_bytes()
                by_snapshot[snapshot] = {}
                manifests[snapshot] = (base, manifest)
                for p, st in paths:
                    files[p] = (st, snapshot, dest)
        emit('Scanned {} payload paths in {} eligible snapshots'.format(len(files), len(by_snapshot)))
        writers = open_writers() if files else set()
        eligible_aliases = collections.Counter(identity(st) for st, _, _ in files.values())
        inodes = collections.defaultdict(list)
        for p, (st, snapshot, dest) in files.items():
            if st.st_ino in EXCLUDED_INODES or eligible_aliases[identity(st)] != st.st_nlink:
                result['skipped']['external_or_uninspected_alias'] += 1
            elif identity(st) in writers:
                result['skipped']['open_for_writing'] += 1
            else:
                inodes[identity(st)].append(p)
        sizes = collections.defaultdict(list)
        for key, paths in inodes.items():
            sizes[files[paths[0]][0].st_size].append(key)
        groups = collections.defaultdict(list)
        for size, keys in sizes.items():
            if len(keys) < 2:
                continue
            for key in keys:
                p = inodes[key][0]
                try:
                    sha, st = checked_hash(p, files[p][0])
                    groups[(st.st_dev, size, sha)].append(key)
                except (OSError, ValueError):
                    result['skipped']['changed_since_scan'] += len(inodes[key])
        groups = [((dev, size, sha), keys) for (dev, size, sha), keys in groups.items() if len(keys) > 1]
        emit('Rehashed {} duplicate groups; planning replacements'.format(len(groups)))
        plans = []
        for (_, _, sha), keys in groups:
            keys.sort(key=lambda k: (getattr(files[inodes[k][0]][0], 'st_birthtime',
                                           files[inodes[k][0]][0].st_mtime), k[1]))
            canonical = inodes[keys[0]][0]
            canonical_stat = files[canonical][0]
            canonical_meta = metadata(canonical, canonical_stat)
            for key in keys[1:]:
                paths = inodes[key]
                for p in paths:
                    st, snapshot, dest = files[p]
                    if new_snapshot and snapshot != Path(new_snapshot):
                        continue
                    if metadata(p, st) != canonical_meta:
                        result['skipped']['incompatible_metadata'] += 1
                        continue
                    plans.append((p, canonical, st, canonical_stat, sha, snapshot, dest))
        touched = {item[5] for item in plans}
        # Existing full physical snapshot verifier establishes the before/after
        # inventory, even for committed historical manifests marked unverified.
        # Failed historical harvests are not upgraded to verified by dedupe.
        before = {}
        for snapshot in sorted(touched):
            base, manifest = manifests[snapshot]
            before[snapshot] = harvest.snapshot_inventory(base, snapshot)
        emit('Verified baseline inventories for {} snapshots; {} candidate paths'.format(len(touched), len(plans)))
        remaining = {key: files[paths[0]][0].st_nlink for key, paths in inodes.items()}
        canonical_link_counts = {key: files[paths[0]][0].st_nlink for key, paths in inodes.items()}
        touched_actual = set()
        last_writers = 0
        try:
            for index, (p, canonical, st, canonical_stat, sha, snapshot, dest) in enumerate(plans):
                if time.monotonic() - last_writers > 2:
                    writers = open_writers()
                    last_writers = time.monotonic()
                if identity(st) in writers or identity(canonical_stat) in writers:
                    result['skipped']['open_for_writing'] += 1
                    continue
                if (stamp(p.lstat()) != stamp(st) or stamp(canonical.lstat()) != stamp(canonical_stat)
                        or p.lstat().st_nlink != (remaining[identity(st)] if apply else st.st_nlink)):
                    result['skipped']['changed_since_hash'] += 1
                    continue
                # Only the last alias releases an inode's allocated blocks.
                freed = st.st_blocks * 512 if remaining[identity(st)] == 1 else 0
                result['candidate_paths'] += 1
                result['candidate_bytes'] += freed
                if apply:
                    try:
                        replace(p, canonical, st, canonical_stat, sha, harvest.local_path(dest, JOURNAL), freed,
                                canonical_link_counts[identity(canonical_stat)], remaining[identity(st)])
                    except (OSError, ValueError) as error:
                        # A failed journal append after rename must propagate;
                        # its receipt is durable and recoverable on the next run.
                        if p.lstat().st_ino == canonical_stat.st_ino:
                            touched_actual.add(snapshot)
                            raise
                        result['skipped'][str(error) if isinstance(error, ValueError) else type(error).__name__] += 1
                        continue
                    canonical_link_counts[identity(canonical_stat)] += 1
                    result['replaced_paths'] += 1
                    result['bytes_freed'] += freed
                    touched_actual.add(snapshot)
                remaining[identity(st)] -= 1
                if index and index % 1000 == 0:
                    emit('Processed {}/{} paths; {:.3f} GB released'.format(index, len(plans), result['bytes_freed'] / 1e9))
        finally:
            for snapshot in sorted(touched_actual):
                base, manifest = manifests[snapshot]
                inventory = harvest.snapshot_inventory(base, snapshot)
                if inventory != before[snapshot]:
                    raise ValueError('snapshot verification failed: ' + str(snapshot))
                result['verification'].append(dict(snapshot=str(snapshot), files=len(inventory), passed=True))
            result['touched_snapshots'] = list(map(str, sorted(touched_actual)))
            for path, content in metadata_before.items():
                if path.read_bytes() != content:
                    raise ValueError('manifest/index changed: ' + str(path))
            result['metadata_files_unchanged'] = len(metadata_before)
            for dest in destinations:
                if dest.is_dir():
                    result['df_after'][str(dest)] = os.statvfs(dest).f_bavail * os.statvfs(dest).f_frsize
    result['skipped'] = dict(result['skipped'])
    return result


def maintenance(root, pod):
    """Only a newly durably verified harvest triggers automatic consolidation."""
    rows = harvest.rows(harvest.state_root(root) / 'HARVEST.jsonl')
    latest = next((r for r in reversed(rows) if harvest.identity(r, pod)), None)
    if latest is None or latest.get('verified') is not True or not latest.get('snapshot'):
        return None
    return run([pod], apply=True, name=pod['name'], new_snapshot=latest['snapshot'],
               progress=lambda message: print('rh pods dedupe: ' + message, flush=True))
