"""Offline byte preservation, writer exclusion and atomic-replacement tests."""
import hashlib
import json
import os
from pathlib import Path
import fcntl
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harvest
import harvest_dedupe as d
import pod_maintenance
import rh


@pytest.fixture
def env(tmp_path, monkeypatch):
    pod = dict(name='p', pod_id='id', lane='team', results=['/workspace/results'],
               harvest_dest=str(tmp_path / 'dest'))
    dest = Path(pod['harvest_dest'])
    base = dest / 'p'
    paths = []
    for i in range(2):
        snap = base / 'snapshots' / str(i)
        path = snap / 'workspace/results/data'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'identical payload\n')
        source = '/workspace/results/data'
        record = dict(pod='p', pod_id='id', lane='team', verified=True, snapshot_format=2,
                      scope=harvest.scope(pod), utc='2026-11-03T12:00:00+00:00',
                      files_index={source: dict(size=path.stat().st_size,
                          sha256=harvest.digest(path)[1], stored=str(path.relative_to(base)))})
        (snap / 'MANIFEST.json').write_text(json.dumps(record))
        paths.append(path)
    (base / '.harvest.lock').touch()
    (base / 'INDEX.json').write_text('{"untouched":true}')
    monkeypatch.setattr(d, 'open_writers', lambda: set())
    return SimpleNamespace(root=tmp_path, pod=pod, dest=dest, base=base, paths=paths)


def run(e, **kwargs):
    return d.run([e.pod], **kwargs)


def test_identical_linked_different_untouched_and_journal_complete(env):
    a, b = env.paths
    other = b.with_name('different')
    other.write_bytes(b'different payload\n')
    inode = other.stat().st_ino
    before_meta = {p: p.read_bytes() for p in env.base.rglob('*.json')}
    old = b.stat()
    result = run(env, apply=True)
    assert result['replaced_paths'] == 1
    assert a.stat().st_ino == b.stat().st_ino
    assert other.stat().st_ino == inode
    assert all(p.read_bytes() == content for p, content in before_meta.items())
    records = harvest.rows(env.dest / d.JOURNAL)
    assert records == [dict(path=str(b), old_inode=old.st_ino, canonical_inode=a.stat().st_ino,
                           sha256=hashlib.sha256(a.read_bytes()).hexdigest(),
                           bytes_freed=old.st_blocks * 512, old_mtime_ns=old.st_mtime_ns)]
    assert result['bytes_freed'] == old.st_blocks * 512
    assert result['verification'][0]['passed']
    assert harvest.digest(b) == (old.st_size, records[0]['sha256'])


def test_dry_run_changes_nothing(env):
    def inventory():
        return {p: (p.lstat().st_ino, p.lstat().st_mtime_ns, p.read_bytes())
                for p in env.dest.rglob('*') if p.is_file()}
    before = inventory()
    result = run(env)
    assert result['candidate_paths'] == 1 and not result['replaced_paths']
    assert inventory() == before


def test_changed_during_hash_is_skipped(env, monkeypatch):
    original = d.checked_hash
    def changed(path, expected=None):
        if path == env.paths[1]:
            path.write_bytes(b'changed')
        return original(path, expected)
    monkeypatch.setattr(d, 'checked_hash', changed)
    assert run(env, apply=True)['replaced_paths'] == 0
    assert len({p.stat().st_ino for p in env.paths}) == 2


@pytest.mark.parametrize('kind', ['locked', 'temporary', 'unfinished'])
def test_locked_or_in_progress_snapshot_skipped(env, kind):
    with (env.base / '.harvest.lock').open('r') as lock:
        if kind == 'locked':
            fcntl.flock(lock, fcntl.LOCK_EX)
        elif kind == 'temporary':
            env.paths[1].with_name('.copy.tmp').write_bytes(b'copying')
        else:
            (env.paths[1].parents[2] / 'MANIFEST.json').rename(env.root / 'saved-manifest')
        assert run(env, apply=True)['replaced_paths'] == 0
    assert all(p.exists() for p in env.paths)


def test_interruption_between_link_and_rename_keeps_every_path(env, monkeypatch):
    original = os.replace
    def interrupt(src, target):
        if Path(src).name.endswith('.tmp') and Path(src).name.startswith('.harvest-dedupe-'):
            raise KeyboardInterrupt
        return original(src, target)
    monkeypatch.setattr(os, 'replace', interrupt)
    before = [p.read_bytes() for p in env.paths]
    with pytest.raises(KeyboardInterrupt):
        run(env, apply=True)
    assert [p.read_bytes() for p in env.paths] == before
    assert len({p.stat().st_ino for p in env.paths}) == 2
    assert not list(env.dest.rglob('.harvest-dedupe-*'))
    assert not (env.dest / d.JOURNAL).exists()


def test_snapshot_verification_rehashes_every_touched_snapshot(env, monkeypatch):
    original, verified = harvest.snapshot_inventory, []
    def inventory(base, snapshot):
        verified.append(snapshot)
        return original(base, snapshot)
    monkeypatch.setattr(harvest, 'snapshot_inventory', inventory)
    result = run(env, apply=True)
    touched = Path(result['touched_snapshots'][0])
    assert verified.count(touched) == 2
    manifest = json.loads((touched / 'MANIFEST.json').read_text())
    for item in manifest['files_index'].values():
        assert harvest.digest(env.base / item['stored']) == (item['size'], item['sha256'])


def test_open_writer_and_external_alias_excluded(env, monkeypatch):
    monkeypatch.setattr(d, 'open_writers', lambda: {d.identity(env.paths[1].stat())})
    assert run(env, apply=True)['replaced_paths'] == 0
    monkeypatch.setattr(d, 'open_writers', lambda: set())
    os.link(env.paths[1], env.root / 'outside')
    assert run(env, apply=True)['replaced_paths'] == 0


def test_different_mode_preserved_by_skipping(env):
    env.paths[1].chmod(0o600)
    assert run(env, apply=True)['replaced_paths'] == 0
    assert env.paths[1].stat().st_mode & 0o777 == 0o600


def test_all_aliases_replaced_and_bytes_counted_once(env):
    alias = env.paths[1].with_name('alias')
    os.link(env.paths[1], alias)
    blocks = alias.stat().st_blocks * 512
    result = run(env, apply=True)
    assert result['replaced_paths'] == 2 and result['bytes_freed'] == blocks
    assert len({p.stat().st_ino for p in [*env.paths, alias]}) == 1
    assert sum(r['bytes_freed'] for r in harvest.rows(env.dest / d.JOURNAL)) == blocks


def test_crash_after_rename_recovered_without_duplicate_journal(env, monkeypatch):
    original = harvest.append
    def fail(path, record):
        if path.name == d.JOURNAL:
            raise OSError('simulated crash after rename')
        return original(path, record)
    monkeypatch.setattr(harvest, 'append', fail)
    with pytest.raises(OSError):
        run(env, apply=True)
    assert all(p.exists() for p in env.paths)
    assert env.paths[0].stat().st_ino == env.paths[1].stat().st_ino
    monkeypatch.setattr(harvest, 'append', original)
    def trash(path):
        path.rename(env.root / ('trashed-' + path.name))
    monkeypatch.setattr(harvest, 'trash_snapshot', trash)
    run(env, apply=True)
    assert len(harvest.rows(env.dest / d.JOURNAL)) == 1
    run(env, apply=True)
    assert len(harvest.rows(env.dest / d.JOURNAL)) == 1


def test_auto_dedupe_restricted_to_new_snapshot_of_same_pod_lane(env):
    old_inode = env.paths[0].stat().st_ino
    harvest.append(harvest.state_root(env.root) / 'HARVEST.jsonl',
        dict(pod='p', pod_id='id', lane='team', scope=harvest.scope(env.pod),
             verified=True, snapshot=str(env.paths[1].parents[2])))
    result = d.maintenance(env.root, env.pod)
    assert result['replaced_paths'] == 1
    assert env.paths[0].stat().st_ino == old_inode == env.paths[1].stat().st_ino


def test_maintenance_worker_invokes_dedupe_after_success(env, monkeypatch):
    calls = []
    fake = SimpleNamespace(registry=lambda root: [env.pod],
        harvest=SimpleNamespace(run=lambda *a, **kw: calls.append('harvest') or 0),
        reap=lambda *a, **kw: calls.append('reap') or 0)
    monkeypatch.setattr(d, 'maintenance', lambda *a: calls.append('dedupe'))
    assert pod_maintenance.run(env.root, 'p', 'id', rh, fake) == 0
    assert calls == ['harvest', 'dedupe', 'reap']


def test_lsof_parser_protects_writable_aliases(monkeypatch):
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0,
        stdout='p1\nf4\naw\nD0x123\ni42\nf5\nar\nD0x123\ni43\nf6\nau\nD0x124\ni44\n'))
    assert d.open_writers() == {(0x123, 42), (0x124, 44)}


def test_cli_dry_run_default_and_pod_selection(env, monkeypatch, capsys):
    import pods
    monkeypatch.setattr(pods, 'registry', lambda root: [env.pod])
    assert rh.main(['--root', str(env.root), 'pods', 'dedupe', '--pod', 'p']) == 0
    assert json.loads(capsys.readouterr().out)['apply'] is False
    assert not (env.dest / d.JOURNAL).exists()


def test_changed_after_hash_before_rename_skips_without_losing_path(env, monkeypatch):
    original = os.link
    def change(src, target, **kwargs):
        original(src, target, **kwargs)
        if Path(target).name.startswith('.harvest-dedupe-'):
            env.paths[1].write_bytes(b'writer changed this copy')
    monkeypatch.setattr(os, 'link', change)
    result = run(env, apply=True)
    assert result['replaced_paths'] == 0
    assert env.paths[1].read_bytes() == b'writer changed this copy'
    assert env.paths[0].read_bytes() == b'identical payload\n'


def test_inode_with_alias_in_in_progress_snapshot_is_excluded(env):
    alias = env.paths[1].with_name('alias')
    os.link(env.paths[0], alias)
    env.paths[0].with_name('.copy.tmp').write_bytes(b'copying')
    assert run(env, apply=True)['replaced_paths'] == 0


def test_dry_run_accounts_multiple_aliases_without_mutation(env):
    alias = env.paths[1].with_name('alias')
    os.link(env.paths[1], alias)
    result = run(env)
    assert result['candidate_paths'] == 2
    assert result['candidate_bytes'] == alias.stat().st_blocks * 512
    assert alias.stat().st_ino == env.paths[1].stat().st_ino != env.paths[0].stat().st_ino


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS extended ACLs')
def test_extended_acl_difference_skipped(env):
    subprocess.run(['/bin/chmod', '+a', 'everyone allow read', str(env.paths[1])], check=True)
    result = run(env, apply=True)
    assert result['replaced_paths'] == 0
    assert result['skipped']['incompatible_metadata'] == 1


def test_process_death_between_link_and_rename_leaves_paths_present(env):
    # os._exit bypasses finally blocks, exercising an actual crash boundary.
    code = '''import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import harvest_dedupe as d
p, c, journal = map(Path, sys.argv[2:])
original = os.replace
def crash(src, target):
    if Path(src).name.startswith('.harvest-dedupe-') and Path(src).suffix == '.tmp':
        os._exit(73)
    return original(src, target)
os.replace = crash
d.replace(p, c, p.stat(), c.stat(), d.checked_hash(c)[0], journal, p.stat().st_blocks * 512)
'''
    a, b = env.paths
    result = subprocess.run([sys.executable, '-c', code, str(Path(d.__file__).parent),
                             str(b), str(a), str(env.dest / d.JOURNAL)])
    assert result.returncode == 73
    assert a.read_bytes() == b.read_bytes() == b'identical payload\n'
    assert a.stat().st_ino != b.stat().st_ino
    assert list(b.parent.glob('.harvest-dedupe-*.tmp'))
    # A later dry-run leaves interrupted artifacts and logical paths intact.
    assert run(env)['replaced_paths'] == 0


def test_automatic_dedupe_ignores_failed_harvest(env):
    harvest.append(harvest.state_root(env.root) / 'HARVEST.jsonl',
        dict(pod='p', pod_id='id', lane='team', scope=harvest.scope(env.pod),
             verified=False, snapshot=str(env.paths[1].parents[2])))
    assert d.maintenance(env.root, env.pod) is None
    assert env.paths[0].stat().st_ino != env.paths[1].stat().st_ino


def test_oldest_inode_uses_birth_time_not_replaced_file_mtime(env):
    first, second = env.paths
    old_second_mtime = second.stat().st_mtime_ns
    # Moving macOS mtime before birth time also moves birth time backwards.
    # Move the first file's mtime forwards to separate mtime from creation order.
    future = first.stat().st_mtime_ns + 10_000_000_000
    os.utime(first, ns=(future, future))
    original_inode = first.stat().st_ino
    run(env, apply=True)
    assert first.stat().st_ino == second.stat().st_ino == original_inode
    assert harvest.rows(env.dest / d.JOURNAL)[0]['old_mtime_ns'] == old_second_mtime


def test_audited_external_alias_inode_explicitly_excluded(env, monkeypatch):
    monkeypatch.setattr(d, 'EXCLUDED_INODES', {env.paths[0].stat().st_ino})
    assert run(env, apply=True)['replaced_paths'] == 0


def test_symlink_manifest_is_not_followed(env, monkeypatch):
    manifest = env.paths[1].parents[2] / 'MANIFEST.json'
    outside = env.root / 'external-manifest'
    manifest.rename(outside)
    manifest.symlink_to(outside)
    original = Path.read_text
    def guarded(path, *a, **kw):
        assert path != manifest, 'symlink manifest must not be read'
        return original(path, *a, **kw)
    monkeypatch.setattr(Path, 'read_text', guarded)
    assert run(env, apply=True)['replaced_paths'] == 0


def test_symlink_journal_is_not_written(env):
    outside = env.root / 'external-journal'
    outside.write_text('external bytes')
    (env.dest / d.JOURNAL).symlink_to(outside)
    assert run(env, apply=True)['replaced_paths'] == 0
    assert outside.read_text() == 'external bytes'
    assert env.paths[0].stat().st_ino != env.paths[1].stat().st_ino


def test_automatic_dedupe_skips_registration_changed_during_harvest(env, monkeypatch):
    current = [env.pod]
    calls = []
    def harvest_run(*args, **kwargs):
        calls.append('harvest')
        current[:] = [dict(env.pod, harvest_dest=str(env.root / 'new-dest'))]
        return 0
    fake = SimpleNamespace(registry=lambda root: current,
        harvest=SimpleNamespace(run=harvest_run),
        reap=lambda *a, **kw: calls.append('reap') or 0)
    monkeypatch.setattr(d, 'maintenance', lambda *a: calls.append('dedupe'))
    assert pod_maintenance.run(env.root, 'p', 'id', rh, fake) == 0
    assert calls == ['harvest']
