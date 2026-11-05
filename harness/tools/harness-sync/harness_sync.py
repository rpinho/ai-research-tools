#!/usr/bin/env python3
"""harness-sync: install the harness from one source, the repo, and notice any drift.

The installed copy and the repo drifted for days: fixes went straight into the installed
files, other fixes went only into the repo, and each copy aged differently. This makes the
committed repo the only source. It installs the git-tracked files under harness/ at a
committed ref (default: main), never the working tree, so a half-finished edit cannot reach
running lanes. Installed code is made read-only so a hand edit fails loudly instead of
drifting quietly. Lanes, ledgers and other runtime state in the installed root are never
touched.

  harness-sync --check   report drift, change nothing; exit 2 if any
  harness-sync --auto    install only when the installed copy is merely behind the ref
                         (no hand edits, no stray modules, no harness job running);
                         otherwise report and exit 2. Run by the scheduled tick.
  harness-sync           install the ref; hand-edited and stray files are backed up first

Every file it replaces or removes is copied to --backups/<utc>/ first; removals go to Trash.
The installed commit is recorded in <root>/.harness-sync.json.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import storage  # noqa: E402  (Trash-only deletion; refuses when no Trash command exists)

RECEIPT = '.harness-sync.json'
CODE_SUFFIXES = {'.py', '.sh'}
SKIP_DIRS = {'lanes', '__pycache__', 'alerts', '.pytest_cache'}


def git(repo, *args, data=None):
    return subprocess.run(['git', '-C', str(repo), *args], input=data, capture_output=True,
                          check=True).stdout


def blob_id(data):
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


def manifest(repo, ref):
    """{relative path: (mode, blob id)} for tracked files under harness/ at ref."""
    out = git(repo, 'ls-tree', '-r', '-z', '--full-tree', ref, 'harness/')
    files = {}
    for entry in filter(None, out.split(b'\0')):
        meta, path = entry.split(b'\t', 1)
        mode, kind, oid = meta.decode().split()
        if kind == 'blob' and mode != '120000':
            files[path.decode()[len('harness/'):]] = (mode, oid)
    return files


def contents(repo, oids):
    """Read blobs in one git cat-file --batch call."""
    if not oids:
        return {}
    out = git(repo, 'cat-file', '--batch', data=''.join(o + '\n' for o in oids).encode())
    blobs, at = {}, 0
    for oid in oids:
        header_end = out.index(b'\n', at)
        size = int(out[at:header_end].split()[2])
        blobs[oid] = out[header_end + 1:header_end + 1 + size]
        at = header_end + 1 + size + 1
    return blobs


def stray_code(root, expected):
    """Installed code files the ref does not have (a module added by hand, say).
    Backup copies (*.bak*) and runtime state are ignored."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith('.')]
        for name in filenames:
            rel = (rel_dir / name).as_posix()
            if '.bak' in name or Path(name).suffix not in CODE_SUFFIXES or rel in expected:
                continue
            found.append(rel)
    return sorted(found)


def running_jobs():
    """launchd harness jobs that currently have a PID (label only, never command lines)."""
    try:
        out = subprocess.run(['launchctl', 'list'], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    jobs = []
    for line in out.splitlines()[1:]:
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0] != '-' and 'research-harness' in parts[2]:
            jobs.append(parts[2])
    return jobs


def plan(repo, root, ref):
    files = manifest(repo, ref)
    receipt = {}
    try:
        receipt = json.loads((root / RECEIPT).read_text())
    except (FileNotFoundError, ValueError):
        pass
    previous = {}
    if receipt.get('commit'):
        try:
            previous = manifest(repo, receipt['commit'])
        except subprocess.CalledProcessError:
            previous = {}
    rows = []  # (status, path)
    for rel, (mode, oid) in sorted(files.items()):
        dest = root / rel
        if not dest.is_file():
            rows.append(('missing', rel))
            continue
        have = blob_id(dest.read_bytes())
        if have == oid:
            if (mode == '100755') != os.access(dest, os.X_OK):
                rows.append(('behind', rel))
            continue
        # Matches what the last install wrote (or the file did not exist then): the
        # installed copy is just older than the ref. Anything else was edited in place.
        old = previous.get(rel)
        rows.append(('behind' if old and old[1] == have else 'edited', rel))
    for rel in sorted(set(previous) - set(files)):
        if (root / rel).is_file():
            rows.append(('removed', rel))
    for rel in stray_code(root, set(files) | set(previous)):
        rows.append(('stray', rel))
    return files, receipt, rows


def install(repo, root, ref, files, rows, backups):
    commit = git(repo, 'rev-parse', ref + '^{commit}').decode().strip()
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup_root = backups / stamp
    todo = {rel for status, rel in rows if status in ('missing', 'behind', 'edited')}
    blobs = contents(repo, sorted({files[rel][1] for rel in todo}))

    def back_up(rel):
        src = root / rel
        if src.is_file():
            dest = backup_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)

    for rel in sorted(todo):
        mode, oid = files[rel]
        dest = root / rel
        back_up(rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name('.' + dest.name + '.harness-sync-tmp')
        tmp.write_bytes(blobs[oid])
        os.chmod(tmp, 0o555 if mode == '100755' else 0o444)
        os.replace(tmp, dest)  # atomic: a running process keeps the file it opened
        print('installed ' + rel)
    for status, rel in rows:
        if status in ('removed', 'stray'):
            back_up(rel)
            storage.trash(root / rel)  # recoverable from Trash, and copied to the backup directory
            print('retired   {} ({})'.format(rel, status))
    receipt = dict(commit=commit, ref=ref, utc=dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
                   repo=str(repo), files=len(files))
    (root / RECEIPT).write_text(json.dumps(receipt, indent=1) + '\n')
    if backup_root.exists():
        print('backups in ' + str(backup_root))
    print('installed {} at {}'.format(ref, commit[:12]))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--root', default='~/.local/share/research-harness', help='installed harness root')
    p.add_argument('--repo', default=str(Path(__file__).resolve().parents[3]), help='source repo')
    p.add_argument('--ref', default='main', help='committed ref to install (never the working tree)')
    p.add_argument('--backups', default='~/.local/state/harness-sync/backups')
    mode = p.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='report drift, change nothing; exit 2 if any')
    mode.add_argument('--auto', action='store_true', help='install only a clean fast-forward; else exit 2')
    p.add_argument('--allow-running', action='store_true', help='install even while a harness job runs')
    a = p.parse_args(argv)
    root = Path(a.root).expanduser()
    repo = Path(a.repo).expanduser()
    files, receipt, rows = plan(repo, root, a.ref)
    commit = git(repo, 'rev-parse', a.ref + '^{commit}').decode().strip()
    print('repo {} {} | installed {}'.format(a.ref, commit[:12], (receipt.get('commit') or 'unknown')[:12]))
    for status, rel in rows:
        print('{:<9} {}'.format(status.upper(), rel))
    if not rows:
        print('installed copy matches {} ({} files)'.format(a.ref, len(files)))
        return 0
    if a.check:
        return 2
    unsafe = [r for r in rows if r[0] in ('edited', 'stray')]
    if a.auto and unsafe:
        print('not installing: {} file(s) were changed in the installed copy; '
              'commit them to the repo or run harness-sync by hand'.format(len(unsafe)))
        return 2
    jobs = running_jobs()
    if jobs and not a.allow_running:
        print('not installing: harness job running: ' + ', '.join(jobs))
        return 2
    install(repo, root, a.ref, files, rows, Path(a.backups).expanduser())
    return 0


if __name__ == '__main__':
    sys.exit(main())
