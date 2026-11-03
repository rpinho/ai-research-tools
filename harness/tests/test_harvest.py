"""Offline harvest safety, corruption, scheduling and alert regressions."""
import base64
import contextlib
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harvest as h
import harvest_reader as reader
import pods
import rh

ORIGINAL_PROXY = h.Proxy


@pytest.fixture(autouse=True)
def no_live(monkeypatch, tmp_path):
    # Tests use reversible temporary moves, never the host Trash service.
    def trash(path):
        dest = tmp_path / 'test-trash' / path.name
        dest.parent.mkdir(exist_ok=True)
        path.rename(dest)
    monkeypatch.setattr(h.storage, 'trash', trash)
    monkeypatch.setattr(h, 'notify', Mock(return_value=True))
    monkeypatch.setattr(h, 'Proxy', Mock(side_effect=AssertionError('live SSH forbidden')))
    monkeypatch.setattr(h.shutil, 'disk_usage', lambda _: SimpleNamespace(free=100_000_000_000))


@pytest.fixture
def setup(tmp_path):
    now = [dt.datetime(2026, 11, 2, 23, tzinfo=dt.timezone.utc)]
    clock = SimpleNamespace(now=lambda: now[0].isoformat())
    pod = dict(name='pod-a', pod_id='id1', lane='team', results=['/workspace/worker-a'],
               harvest_dest=str(tmp_path / 'lanes/team/results/pod-harvest'), ssh_target=('id1-a' + chr(64) + 'ssh.runpod.io'),
               vcpus=32, gpus=0, dollars_per_hour=.96, owner_note='fixture')
    content = {'/workspace/worker-a/results/x.json': b'{"result":42}\n'}
    class Fake:
        calls = []
        extra = []
        corrupt = False
        interrupted = False
        deadline = 7200
        def __init__(self, pod):
            self.pod = pod
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def manifest(self):
            for p, b in content.items():
                yield dict(path=p, size=len(b), sha256=hashlib.sha256(b).hexdigest())
            yield from self.extra
            yield dict(end=True, deadline_seconds=self.deadline)
        def download(self, item, target):
            self.calls.append(item['path'])
            target.write(b'corrupt' if self.corrupt else content[item['path']])
            if self.interrupted:
                raise OSError('private SSH banner MUST NOT APPEAR')
    return tmp_path, pod, content, Fake, clock, now


def run(setup, **kw):
    root, pod, _, fake, clock, _ = setup
    with contextlib.redirect_stdout(io.StringIO()):
        return h.harvest_one(root, pod, clock, transport=fake, **kw)


def test_incremental_preserves_revisions_and_deleted_sources(setup):
    root, pod, content, fake, clock, now = setup
    assert run(setup)['verified']
    first = list(Path(pod['harvest_dest']).rglob('x.json'))[0]
    assert run(setup)['unchanged'] == 1
    assert len(fake.calls) == 1
    assert first.read_bytes() == b'{"result":42}\n'
    content['/workspace/worker-a/results/x.json'] = b'new value'
    assert run(setup)['files'] == 1
    assert any(p.read_bytes() == b'{"result":42}\n' for p in Path(pod['harvest_dest']).rglob('x.json'))
    content.clear()
    assert run(setup)['verified']
    assert len(list(Path(pod['harvest_dest']).rglob('x.json'))) == 2
    index = json.loads((Path(pod['harvest_dest']) / 'pod-a/INDEX.json').read_text())
    assert '/workspace/worker-a/results/x.json' in index['files']


@pytest.mark.parametrize('kind', ['corrupt', 'interrupted'])
def test_bad_transfer_is_never_committed_and_old_copy_survives(setup, kind):
    root, pod, content, fake, *_ = setup
    assert run(setup)['verified']
    content['/workspace/worker-a/results/x.json'] = b'changed'
    setattr(fake, kind, True)
    h.notify.return_value = True
    row = run(setup)
    assert not row['verified'] and row['files'] == 0
    assert len(list(Path(pod['harvest_dest']).rglob('x.json'))) == 1
    assert not list(Path(pod['harvest_dest']).rglob('*.tmp'))
    assert 'private SSH' not in (h.state_root(root) / 'HARVEST.jsonl').read_text()


def test_unchanged_local_copy_is_rehashed_and_repaired(setup):
    root, pod, content, fake, *_ = setup
    run(setup)
    saved = list(Path(pod['harvest_dest']).rglob('x.json'))[0]
    saved.write_bytes(b'local corruption')
    assert run(setup)['files'] == 1
    assert len(fake.calls) == 2
    assert saved.read_bytes() == b'local corruption'  # never overwrite history


def test_guards_skips_are_logged_without_download(setup):
    root, pod, _, fake, *_ = setup
    fake.extra = [dict(path='/workspace/worker-a/big', skip='over_200_MB', size=h.MAX_BYTES+1),
                  dict(path='/workspace/worker-a/movie.mp4', skip='media', size=12)]
    row = run(setup)
    assert row['verified'] and len(row['skipped']) == 2
    assert fake.calls == ['/workspace/worker-a/results/x.json']


@pytest.mark.parametrize('bad', ['/workspace', '/workspace/', '/workspace/../root', '/etc', '/workspace/a/../b', '/workspace/a\nx'])
def test_invalid_paths_rejected_before_transport(setup, bad):
    root, pod, _, fake, *_ = setup
    pod['results'] = [bad]
    assert not run(setup)['verified']
    assert not fake.calls


def test_escape_and_local_symlink_are_refused(setup, tmp_path):
    root, pod, _, fake, *_ = setup
    fake.extra = [dict(path='/workspace/other/private', size=1, sha256='a'*64)]
    assert not run(setup)['verified']
    assert fake.calls == ['/workspace/worker-a/results/x.json']
    fake.extra = []
    pod['harvest_dest'] += '-symlink-test'
    dest = Path(pod['harvest_dest'])
    dest.mkdir(parents=True, exist_ok=True)
    (dest / 'pod-a').symlink_to(tmp_path / 'elsewhere')
    assert not run(setup)['verified']
    assert not (tmp_path / 'elsewhere').exists()


def test_alert_after_two_failures_throttles_retries_and_resets(setup):
    root, pod, content, fake, clock, now = setup
    fake.extra = [dict(path='/workspace/worker-a/missing', error='unavailable')]
    h.notify.side_effect = None
    h.notify.return_value = True
    run(setup)
    h.notify.assert_not_called()
    now[0] += dt.timedelta(minutes=15)
    run(setup)
    assert h.notify.call_count == 1
    run(setup)
    assert h.notify.call_count == 1
    now[0] += dt.timedelta(hours=6)
    h.notify.return_value = False
    run(setup)
    h.notify.return_value = True
    run(setup)
    assert h.notify.call_count == 3
    fake.extra = []
    run(setup)
    fake.extra = [dict(path='/workspace/worker-a/missing', error='unavailable')]
    now[0] += dt.timedelta(hours=6)
    run(setup)
    assert h.notify.call_count == 3


def test_lint_freshness_missing_and_replaced_pod(setup):
    root, pod, _, fake, clock, now = setup
    assert '45 min' in h.lint(root, [pod], clock)[0]
    run(setup)
    assert not h.lint(root, [pod], clock)
    now[0] += dt.timedelta(minutes=45)
    assert not h.lint(root, [pod], clock)
    now[0] += dt.timedelta(seconds=1)
    assert h.lint(root, [pod], clock)
    pod['pod_id'] = 'replacement'
    now[0] -= dt.timedelta(minutes=30)
    assert h.lint(root, [pod], clock)
    pod.pop('results')
    assert any('no results' in s for s in h.lint(root, [pod], clock))
    lane = root / 'lanes/restricted-a'
    lane.mkdir(parents=True)
    assert pods.lint(lane, clock) == []


def test_deadline_windows_dedup_and_restart(setup):
    root, pod, _, fake, clock, now = setup
    pod['ends_utc'] = (now[0] + dt.timedelta(minutes=21)).isoformat()
    with contextlib.redirect_stdout(io.StringIO()):
        assert h.run(root, [pod], clock, due=True, transport=fake) == 0
        assert not fake.calls
        now[0] += dt.timedelta(minutes=1)
        assert h.run(root, [pod], clock, due=True, transport=fake) == 0
        assert len(fake.calls) == 1
        h.run(root, [pod], clock, due=True, transport=fake)
        logs = h.rows(h.state_root(root) / 'HARVEST.jsonl')
        assert len(logs) == 1
        now[0] += dt.timedelta(minutes=15)
        h.run(root, [pod], clock, due=True, transport=fake)
        assert len(h.rows(h.state_root(root) / 'HARVEST.jsonl')) == 2
        now[0] += dt.timedelta(minutes=5)
        h.run(root, [pod], clock, due=True, transport=fake)
        assert len(h.rows(h.state_root(root) / 'HARVEST.jsonl')) == 2


def test_discovered_deadline_used_locally(setup):
    root, pod, _, fake, clock, now = setup
    run(setup)
    history = h.rows(h.state_root(root) / 'HARVEST.jsonl')
    assert h.latest_deadline(pod, history) == now[0] + dt.timedelta(hours=2)


def test_reader_size_media_symlinks_and_specials(tmp_path, monkeypatch):
    monkeypatch.setattr(reader, 'valid', lambda p: str(p).startswith(str(tmp_path)))
    normal = tmp_path / 'x.json'
    normal.write_bytes(b'{}')
    assert reader.inspect(str(normal))['sha256'] == hashlib.sha256(b'{}').hexdigest()
    huge = tmp_path / 'huge'
    with huge.open('wb') as f:
        f.truncate(h.MAX_BYTES+1)
    assert reader.inspect(str(huge))['skip'] == 'over_200_MB'
    for name, data in [('x.MP4', b'anything'), ('hidden', b'RIFF1234WAVE...')]:
        p = tmp_path / name
        p.write_bytes(data)
        assert reader.inspect(str(p))['skip'] == 'media'
    symlink = tmp_path / 'link'
    symlink.symlink_to(normal)
    with pytest.raises(OSError):
        reader.inspect(str(symlink))
    fifo = tmp_path / 'fifo'
    os.mkfifo(fifo)
    assert reader.inspect(str(fifo))['skip'] == 'special'
    found = list(reader.manifest([str(tmp_path)]))
    assert any(r.get('skip') == 'symlink' for r in found)


def test_timer_inventory_only_reads_sleep_processes(monkeypatch):
    probe = Mock(return_value=SimpleNamespace(stdout='12 4 sleep 86400\n30 1 sleep 9999\n50 5 sleep 60\n'))
    monkeypatch.setattr(reader.subprocess, 'run', probe)
    assert reader.deadline_seconds() == 86388
    assert probe.call_args.args[0] == ['ps', '-C', 'sleep', '-o', 'etimes=,ppid=,args=']


def test_cli_registration_requires_paths_and_accepts_deadline(setup, monkeypatch):
    root, pod, _, fake, clock, now = setup
    (root / 'lanes/theory').mkdir(parents=True)
    monkeypatch.setattr(pods, 'probe', Mock(return_value=dict(vcpus=32, gpus=0)))
    args = ['--root', str(root), 'pods', 'add', 'theory1', '--pod-id', 'theoryid',
            '--ssh-target', ('theoryid-a' + chr(64) + 'ssh.runpod.io'), '--lane', 'theory',
            '--dollars-per-hour', '1', '--owner-note', 'fixture']
    with pytest.raises(SystemExit):
        rh.main(args)
    assert not pods.probe.called
    assert rh.main(args + ['--results', '/workspace/theory/results', '--harvest-dest',
        str(root / 'lanes/theory/results'), '--ends-utc', '2026-11-03T12:00:00Z']) == 0
    saved = pods.registry(root)[0]
    assert saved['ends_utc'] == '2026-11-03T12:00:00Z'


def test_check_calls_harvest_after_utilization(setup, monkeypatch):
    root, pod, _, fake, clock, now = setup
    (root / 'pods.json').write_text(json.dumps([pod]))
    called = []
    monkeypatch.setattr(pods, 'check', lambda *_: called.append('check') or 0)
    monkeypatch.setattr(h, 'run', lambda *_: called.append('harvest') or 1)
    assert rh.main(['--root', str(root), 'pods', 'check']) == 1
    assert called == ['check', 'harvest']


def test_actual_pty_protocol_binary_frames_without_remote_files(tmp_path, monkeypatch):
    """Real local PTY exercises terminal echo, CRLF and shell input limits."""
    import pty
    proxy_class = ORIGINAL_PROXY
    results = tmp_path / 'results'
    results.mkdir()
    data = bytes(range(256)) * 800
    (results / 'result.bin').write_bytes(data)
    for i in range(20):
        (results / ('extra%02d.json' % i)).write_bytes(b'{}')
    deep = results / ('d' * 180)
    deep.mkdir()
    (deep / 'long-request.json').write_bytes(b'{}')
    source = Path(reader.__file__).read_text().replace("path.startswith('/workspace/')", "path.startswith(" + repr(str(tmp_path) + '/') + ")")
    real_popen = subprocess.Popen
    master, slave = pty.openpty()
    child = real_popen(['/bin/bash', '--noprofile', '--norc', '-i'], stdin=slave, stdout=slave, stderr=slave,
                       env=dict(os.environ, PS1='# ', PS2='> '))
    os.close(slave)
    wrapper = SimpleNamespace(stdin=os.fdopen(os.dup(master), 'wb', buffering=0),
        stdout=os.fdopen(master, 'rb', buffering=0), poll=child.poll, wait=child.wait, kill=child.kill)
    popen = Mock(return_value=wrapper)
    monkeypatch.setattr(h.subprocess, 'Popen', popen)
    monkeypatch.setattr(Path, 'read_text', lambda *_a, **_k: source)
    try:
        with proxy_class(dict(results=[str(results)], ssh_target=('offline' + chr(64) + 'ssh.runpod.io')), timeout=15) as proxy:
            count, final = 0, None
            for item in proxy.manifest():
                if 'path' in item:
                    target = io.BytesIO()
                    proxy.download(item, target)
                    expected = data if item['path'].endswith('result.bin') else b'{}'
                    assert target.getvalue() == expected
                    count += 1
                final = item
            assert count == 22
            assert final['end'] is True
        argv = popen.call_args.args[0]
        assert 'StrictHostKeyChecking=yes' in argv
        assert not list(results.glob('*.py'))
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()


def test_scope_change_invalidates_freshness(setup):
    root, pod, _, fake, clock, now = setup
    run(setup)
    assert not h.lint(root, [pod], clock)
    pod['results'].append('/workspace/other')
    assert h.lint(root, [pod], clock)


def test_per_pod_lock_logs_one_failure_and_cli_returns_nonzero(setup, monkeypatch, capsys):
    import fcntl
    root, pod, _, fake, *_ = setup
    (root / 'pods.json').write_text(json.dumps([pod]))
    monkeypatch.setattr(h, 'Proxy', fake)
    base = Path(pod['harvest_dest']) / pod['name']
    base.mkdir(parents=True)
    with (base / '.harvest.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert rh.main(['--root', str(root), 'pods', 'harvest', pod['name']]) == 1
        assert not fake.calls
        history = h.rows(h.state_root(root) / 'HARVEST.jsonl')
        assert len(history) == 1
        assert history[0]['verified'] is False
        assert history[0]['errors'] == [dict(error='PodBusy')]
        assert history[0]['reason'] == 'manual'
        assert h.identity(history[0], pod)
        assert json.loads(capsys.readouterr().out) == history[0]
    # The busy attempt does not satisfy freshness or prevent the next copy.
    assert h.lint(root, [pod], setup[4])
    assert rh.main(['--root', str(root), 'pods', 'harvest', pod['name']]) == 0
    history = h.rows(h.state_root(root) / 'HARVEST.jsonl')
    assert len(history) == 2 and history[-1]['verified'] is True


def test_due_failed_harvest_retries_and_new_timer_generation(setup):
    root, pod, content, fake, clock, now = setup
    pod['ends_utc'] = (now[0] + dt.timedelta(minutes=20)).isoformat()
    fake.corrupt = True
    with contextlib.redirect_stdout(io.StringIO()):
        assert h.run(root, [pod], clock, due=True, transport=fake) == 1
        fake.corrupt = False
        assert h.run(root, [pod], clock, due=True, transport=fake) == 0
        assert len(fake.calls) == 2
        # Renewal is a new generation, rather than a permanently satisfied lead.
        pod['ends_utc'] = (now[0] + dt.timedelta(minutes=35)).isoformat()
        now[0] += dt.timedelta(minutes=15)
        h.run(root, [pod], clock, due=True, transport=fake)
    assert len(h.rows(h.state_root(root) / 'HARVEST.jsonl')) == 3


def test_deadline_discovery_jitter_is_stable(setup):
    root, pod, _, fake, clock, now = setup
    run(setup)
    first = h.latest_deadline(pod, h.rows(h.state_root(root) / 'HARVEST.jsonl'))
    now[0] += dt.timedelta(minutes=15)
    fake.deadline -= 900 - 1
    run(setup)
    assert h.latest_deadline(pod, h.rows(h.state_root(root) / 'HARVEST.jsonl')) == first


def test_live_transport_startup_failure_reaps_local_child(monkeypatch):
    from unittest.mock import MagicMock
    child = MagicMock()
    child.poll.return_value = None
    monkeypatch.setattr(h.subprocess, 'Popen', Mock(return_value=child))
    monkeypatch.setattr(ORIGINAL_PROXY, 'read', Mock(side_effect=TimeoutError('fixture')))
    monkeypatch.setattr(ORIGINAL_PROXY, 'send', Mock(side_effect=TimeoutError('fixture')))
    with pytest.raises(TimeoutError):
        ORIGINAL_PROXY(dict(results=['/workspace/results'], ssh_target=('offline' + chr(64) + 'ssh.runpod.io')))
    child.kill.assert_called_once()
    child.wait.assert_called_once()
    child.stdin.close.assert_called_once()
    child.stdout.close.assert_called_once()


def test_partial_inventory_preserves_progress_but_is_not_a_link_source(setup):
    root, pod, content, fake, clock, now = setup
    normal_manifest = fake.manifest
    def interrupted(self):
        yield next(iter(normal_manifest(self)))
        raise OSError('fixture interruption')
    fake.manifest = interrupted
    result = run(setup)
    assert not result['verified'] and result['files'] == 1
    index_path = Path(pod['harvest_dest']) / 'pod-a/INDEX.json'
    assert '/workspace/worker-a/results/x.json' in json.loads(index_path.read_text())['files']
    fake.manifest = normal_manifest
    result = run(setup)
    assert result['verified'] and result['unchanged'] == 0
    assert len(fake.calls) == 2


def test_notification_throttle_is_per_pod_across_scope_changes(setup):
    root, pod, _, fake, clock, now = setup
    fake.extra = [dict(path='/workspace/worker-a/missing', error='unavailable')]
    run(setup)
    run(setup)
    assert h.notify.call_count == 1
    pod['results'].append('/workspace/new')
    run(setup)
    run(setup)
    assert h.notify.call_count == 1


def test_independent_pods_copy_concurrently_and_log_separately(setup):
    import threading
    root, pod, _, fake, clock, now = setup
    barrier = threading.Barrier(2, timeout=2)
    original = fake.manifest
    def synchronized(self):
        barrier.wait()
        yield from original(self)
    fake.manifest = synchronized
    other = dict(pod, name='pod-b', pod_id='other')
    with contextlib.redirect_stdout(io.StringIO()):
        assert h.run(root, [pod, other], clock, transport=fake) == 0
    logged = h.rows(h.state_root(root) / 'HARVEST.jsonl')
    assert {r['pod'] for r in logged} == {'pod-a', 'pod-b'}
    assert all(r['verified'] for r in logged)


@pytest.mark.parametrize('phase', ['scope', 'start_clock', 'startup', 'manifest', 'close', 'commit', 'end_clock'])
def test_exception_boundary_logs_one_failed_attempt_and_nonzero(setup, monkeypatch, phase):
    root, pod, _, fake, clock, _ = setup
    failure = Mock(side_effect=RuntimeError('private transport detail MUST NOT APPEAR'))
    if phase == 'scope':
        monkeypatch.setattr(h, 'scope', failure)
    elif phase == 'start_clock':
        monkeypatch.setattr(clock, 'now', Mock(side_effect=[RuntimeError('clock'), setup[5][0].isoformat()]))
    elif phase == 'startup':
        monkeypatch.setattr(fake, '__init__', failure)
    elif phase == 'manifest':
        original = fake.manifest
        def broken_manifest(self):
            yield next(iter(original(self)))
            raise RuntimeError('private transport detail MUST NOT APPEAR')
        monkeypatch.setattr(fake, 'manifest', broken_manifest)
    elif phase == 'close':
        monkeypatch.setattr(fake, '__exit__', failure)
    elif phase == 'commit':
        monkeypatch.setattr(h, 'atomic', failure)
    else:
        fake.deadline = None  # no intermediate clock read for deadline metadata
        monkeypatch.setattr(clock, 'now', Mock(side_effect=[setup[5][0].isoformat(), RuntimeError('clock')]))
    assert h.run(root, [pod], clock, name=pod['name'], transport=fake) == 1
    log = h.state_root(root) / 'HARVEST.jsonl'
    history = h.rows(log)
    assert len(history) == 1
    row = history[0]
    assert row['pod'] == pod['name'] and row['verified'] is False
    assert row['errors'] == [dict(error='RuntimeError')]
    assert 'private transport detail' not in log.read_text()
    h.timestamp(row['utc'])
    if phase in ('manifest', 'close', 'commit', 'end_clock'):
        assert row['files'] == 1
        assert list(Path(pod['harvest_dest']).rglob('x.json'))


@pytest.mark.parametrize('error', [KeyboardInterrupt, lambda: SystemExit(0)])
@pytest.mark.parametrize('phase', ['manifest', 'end_clock'])
def test_cancellation_logs_once_then_propagates_nonzero(setup, monkeypatch, error, phase):
    root, pod, _, fake, clock, _ = setup
    if phase == 'manifest':
        monkeypatch.setattr(fake, 'manifest', Mock(side_effect=error()))
    else:
        fake.deadline = None
        monkeypatch.setattr(clock, 'now', Mock(side_effect=[setup[5][0].isoformat(), error()]))
    with pytest.raises((KeyboardInterrupt, SystemExit)) as raised:
        h.run(root, [pod], clock, name=pod['name'], transport=fake)
    if isinstance(raised.value, SystemExit):
        assert raised.value.code != 0
    history = h.rows(h.state_root(root) / 'HARVEST.jsonl')
    assert len(history) == 1 and history[0]['verified'] is False
    assert history[0]['errors'][0]['error'] in ('KeyboardInterrupt', 'SystemExit')


def test_log_write_failure_propagates_without_retry_or_success(setup, monkeypatch):
    root, pod, _, fake, clock, _ = setup
    sink = Mock(side_effect=OSError('log unavailable'))
    monkeypatch.setattr(h, 'append', sink)
    with pytest.raises(OSError, match='log unavailable'):
        h.run(root, [pod], clock, name=pod['name'], transport=fake)
    sink.assert_called_once()
    h.notify.assert_not_called()


def test_state_root_requires_explicit_override_even_for_home_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    native = tmp_path / '.local/share/research-harness'
    native.mkdir(parents=True)
    alias = tmp_path / 'alias'
    alias.symlink_to(native, target_is_directory=True)
    production_state = tmp_path / '.local/state/research-harness'
    assert h.state_root(native) == native / '.harvest-state'
    assert h.state_root(alias) == alias / '.harvest-state'
    isolated = tmp_path / 'isolated'
    assert h.state_root(isolated) == isolated / '.harvest-state'
    monkeypatch.setenv('RH_HARVEST_STATE', str(production_state))
    assert h.state_root(native) == production_state


def manifests(setup):
    return h.snapshots(Path(setup[1]['harvest_dest']) / setup[1]['name'], setup[1])


def test_unchanged_snapshot_is_complete_hardlinked_and_fully_hashed(setup, monkeypatch):
    content = setup[2]
    content['/workspace/worker-a/results/other.json'] = b'another file'
    first = run(setup)
    calls = []
    original = h.digest
    def tracking(path):
        calls.append(path)
        return original(path)
    monkeypatch.setattr(h, 'digest', tracking)
    second = run(setup)
    assert second['verified'] and second['files'] == second['bytes'] == 0
    assert second['linked_files'] == second['verified_files'] == 2
    assert second['linked_bytes'] == second['verified_bytes'] == sum(map(len, content.values()))
    old, new = Path(first['snapshot']), Path(second['snapshot'])
    for source, data in content.items():
        a, b = old / source.lstrip('/'), new / source.lstrip('/')
        assert a.stat().st_ino == b.stat().st_ino and b.read_bytes() == data
        assert b in calls  # final path itself is rehashed, in addition to temp
    record = json.loads((new / 'MANIFEST.json').read_text())
    assert all(item['stored'].startswith(str(new.relative_to(new.parent.parent)) + '/')
               for item in record['files_index'].values())


def test_same_size_changed_hash_copies_and_does_not_modify_linked_history(setup):
    source = next(iter(setup[2]))
    first = run(setup)
    setup[2][source] = b'x' * len(setup[2][source])
    second = run(setup)
    a, b = Path(first['snapshot']) / source.lstrip('/'), Path(second['snapshot']) / source.lstrip('/')
    assert second['verified'] and second['files'] == 1 and second['linked_files'] == 0
    assert a.stat().st_ino != b.stat().st_ino
    assert a.read_bytes() != b.read_bytes()


def test_final_verification_detects_corrupted_hardlink(setup, monkeypatch):
    first = run(setup)
    source = next(iter(setup[2]))
    def corrupt_on_close(self, *_):
        (Path(first['snapshot']) / source.lstrip('/')).write_bytes(b'corrupt after linking')
    monkeypatch.setattr(setup[3], '__exit__', corrupt_on_close)
    row = run(setup)
    assert not row['verified'] and row['linked_files'] == 1
    assert row['errors'] == [dict(path=source, error='snapshot_verification_failed')]


def test_failed_latest_snapshot_cannot_supply_links(setup):
    source = next(iter(setup[2]))
    first = run(setup)
    setup[2][source] = b'changed'
    setup[3].extra = [dict(path='/workspace/worker-a/missing', error='unavailable')]
    assert not run(setup)['verified']
    setup[3].extra = []
    last = run(setup)
    assert last['verified'] and last['files'] == 1 and last['linked_files'] == 0
    assert last['previous_verified_snapshot'] == first['snapshot']


def test_retention_keeps_newest_two_and_logs_removal(setup):
    runs = [run(setup) for _ in range(4)]
    assert [p for p, _ in manifests(setup)] == [Path(r['snapshot']) for r in runs[-2:]]
    removed = [r for r in h.rows(h.state_root(setup[0]) / 'HARVEST.jsonl')
               if r.get('event') == 'SNAPSHOT_REMOVED']
    assert [r['path'] for r in removed] == [r['snapshot'] for r in runs[:2]]
    assert all(r['files'] == 1 and r['bytes'] == len(next(iter(setup[2].values()))) for r in removed)


def test_retention_never_removes_unique_revisions_or_deleted_files(setup):
    source = next(iter(setup[2]))
    unique = run(setup)
    setup[2][source] = b'new revision'
    older = run(setup)
    run(setup)
    run(setup)
    assert Path(unique['snapshot']).exists()
    assert not Path(older['snapshot']).exists()
    setup[2].clear()
    run(setup)
    run(setup)
    run(setup)
    assert Path(unique['snapshot']).exists()
    assert any((p / source.lstrip('/')).exists() for p, _ in manifests(setup))


def test_retention_checks_actual_payload_and_rehashes_kept_copies(setup):
    first = run(setup)
    # An unindexed file is still unique data and prevents pruning.
    (Path(first['snapshot']) / 'unique.txt').write_text('preserve me')
    run(setup)
    run(setup)
    assert Path(first['snapshot']).exists()
    # A formerly verified but corrupted kept file cannot prove redundancy.
    source = next(iter(setup[2]))
    directory = manifests(setup)[-1][0]
    (directory / source.lstrip('/')).write_bytes(b'corrupt')
    h.retain(directory.parent.parent, setup[1], h.state_root(setup[0]) / 'HARVEST.jsonl',
             setup[4].now(), {})
    assert Path(first['snapshot']).exists()


def test_retention_protects_legacy_manifest_references(setup):
    first = run(setup)
    second = run(setup)
    p = Path(second['snapshot']) / 'MANIFEST.json'
    record = json.loads(p.read_text())
    record['snapshot_format'] = 1
    source = next(iter(setup[2]))
    record['files_index'][source]['stored'] = str(Path(first['snapshot']).relative_to(p.parent.parent.parent)) + '/' + source.lstrip('/')
    p.write_text(json.dumps(record))
    (p.parent / 'unique-legacy.txt').write_text('keeps this dependent manifest')
    run(setup)
    run(setup)
    assert Path(first['snapshot']).exists() and Path(second['snapshot']).exists()


def test_low_disk_skips_unchanged_and_large_changes_alerts_once_daily(setup, monkeypatch):
    first = run(setup)
    small = '/workspace/worker-a/results/new.json'
    setup[2][small] = b'small changed result'
    setup[3].extra = [dict(path='/workspace/worker-a/results/checkpoint.pth',
                          size=50_000_000, sha256='a' * 64)]
    monkeypatch.setattr(h.shutil, 'disk_usage', lambda _: SimpleNamespace(free=h.DISK_FLOOR_BYTES-1))
    row = run(setup)
    assert not row['verified'] and row['low_disk'] and not row['errors']
    assert row['files'] == 1 and row['bytes'] == len(setup[2][small])
    assert row['linked_files'] == 0 and len(row['skipped_low_disk']) == 2
    assert set(setup[3].calls) == {next(iter(setup[2])), small}
    alert = setup[0] / 'alerts/harvest-low-disk-pod-a'
    initial = alert.read_bytes()
    setup[5][0] += dt.timedelta(minutes=15)
    run(setup)
    assert alert.read_bytes() == initial
    setup[5][0] += dt.timedelta(days=1)
    run(setup)
    assert alert.read_bytes() != initial
    assert Path(first['snapshot']).exists()


def test_disk_floor_is_checked_before_each_file_and_exact_threshold_allowed(setup, monkeypatch):
    source = next(iter(setup[2]))
    setup[2]['/workspace/worker-a/results/second.json'] = b'new'
    free = iter([h.DISK_FLOOR_BYTES, h.DISK_FLOOR_BYTES, h.DISK_FLOOR_BYTES-1])
    monkeypatch.setattr(h.shutil, 'disk_usage', lambda _: SimpleNamespace(free=next(free)))
    row = run(setup)
    assert not row['verified'] and row['low_disk']
    assert row['files'] == 2  # both are new and under 50 MB
    assert row['verified_files'] == 2


def test_link_failure_falls_back_to_verified_copy(setup, monkeypatch):
    run(setup)
    monkeypatch.setattr(h.os, 'link', Mock(side_effect=OSError('unsupported')))
    row = run(setup)
    assert row['verified'] and row['files'] == 1 and row['linked_files'] == 0


def test_empty_inventory_still_obeys_disk_floor(setup, monkeypatch):
    setup[2].clear()
    monkeypatch.setattr(h.shutil, 'disk_usage', lambda _: SimpleNamespace(free=h.DISK_FLOOR_BYTES-1))
    row = run(setup)
    assert row['low_disk'] and not row['verified'] and not row['errors']
    assert (setup[0] / 'alerts/harvest-low-disk-pod-a').exists()


def test_failed_transfer_bytes_written_are_measured(setup):
    setup[3].interrupted = True
    row = run(setup)
    assert row['bytes'] == 0 and row['bytes_written'] == len(next(iter(setup[2].values())))
    assert not row['verified']


def test_retention_refuses_destinations_outside_pod_harvest(setup):
    setup[1]['harvest_dest'] = str(setup[0] / 'other-results')
    runs = [run(setup) for _ in range(4)]
    assert all(Path(row['snapshot']).exists() for row in runs)


def test_retention_refuses_symlink_payload_in_kept_snapshot(setup):
    first = run(setup)
    (Path(first['snapshot']) / 'unique.txt').write_text('unique')
    second = run(setup)
    run(setup)
    (Path(second['snapshot']) / 'escape').symlink_to(setup[0] / 'outside')
    with pytest.raises(ValueError, match='local symlink'):
        h.retain(Path(second['snapshot']).parent.parent, setup[1],
                 h.state_root(setup[0]) / 'HARVEST.jsonl', setup[4].now(), {})
    assert Path(first['snapshot']).exists()
