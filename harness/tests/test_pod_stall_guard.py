"""Virtual time and local files only: no rental, SSH or real notifications."""
import concurrent.futures
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harvest
import lane_alerts
import pod_guard
import pod_maintenance
import pods
import rh


@pytest.fixture
def env(tmp_path, monkeypatch):
    start = dt.datetime(2026, 11, 3, 20, tzinfo=dt.timezone.utc)
    minute = [0]
    pod = dict(name='guard', pod_id='abc123', lane='theory', vcpus=16, gpus=1,
               dollars_per_hour=5.29, owner_note='offline test', ssh_target=('abc123-1' + chr(64) + 'ssh.runpod.io'),
               results=['/workspace/results'], harvest_dest=str(tmp_path / 'lanes/theory/results/pods'))
    (tmp_path / 'lanes/theory').mkdir(parents=True)
    (tmp_path / 'pods.json').write_text(json.dumps([pod]))
    monkeypatch.setattr(rh, 'now', lambda: (start + dt.timedelta(minutes=minute[0])).isoformat())
    forbidden = Mock(side_effect=AssertionError('live/lifecycle operation forbidden'))
    monkeypatch.setattr(pods, 'probe', forbidden)
    monkeypatch.setattr(pods, 'reap', forbidden)
    monkeypatch.setattr(pods, 'dispatch', forbidden)
    monkeypatch.setattr(pods, 'terminate_self', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    def sample(at, cpu=1, gpu=0, reachable=True, **extra):
        minute[0] = at
        row = dict(utc=rh.now(), pod=pod['name'], pod_id=pod['pod_id'], lane=pod['lane'],
                   reachable=reachable, cpu_frac=cpu, gpu_util=gpu, **extra)
        rh.append(tmp_path / 'PODS.jsonl', row)
        history = rh.rows(tmp_path / 'PODS.jsonl')
        return pods.alerts(tmp_path, history, pod, rh)
    return SimpleNamespace(root=tmp_path, pod=pod, minute=minute, sample=sample, forbidden=forbidden)


def queued():
    return rh.rows(lane_alerts.queue_path())


def test_gpu_zero_for_ten_minutes_alerts_then_repeats_with_accumulated_cost(env):
    e = env
    for at in (0, 5):
        assert e.sample(at) == 0
        assert not queued()
    e.sample(10)
    first = queued()[0]
    assert first['kind'] == 'pod-idle' and first['minutes_idle'] == 10
    assert first['idle_cost'] == pytest.approx(5.29 / 6, abs=.0001)
    for at in range(15, 40, 5):
        e.sample(at)
    assert len(queued()) == 1
    e.sample(40)
    second = queued()[1]
    assert second['minutes_idle'] == 40
    assert second['idle_cost'] == pytest.approx(5.29 * 2 / 3, abs=.0001)
    assert all(row['pod_id'] == e.pod['pod_id'] for row in queued())
    e.forbidden.assert_not_called()


def test_two_gpu_samples_spanning_ten_minutes_suffice(env):
    env.sample(0)
    env.sample(10)
    assert len(queued()) == 1


@pytest.mark.parametrize('lane', sorted(pods.LANES))
def test_routes_both_files_to_every_owning_lane(env, lane):
    e = env
    e.pod['lane'] = lane
    for at in (0, 5, 10):
        e.sample(at)
    row = queued()[0]
    alert = json.loads((e.root / 'lanes' / lane / 'alerts/pod-idle-guard').read_text())
    assert alert == row
    assert row['for'] == 'agents' and row['lane'] == lane
    assert row['pod'] == 'guard' and row['$/h'] == 5.29
    assert row['message'] == pod_guard.ACTION


@pytest.mark.parametrize('cpu', [0, .049, .05, .5, 1])
def test_cpu_idle_requires_below_five_percent_for_twenty_minutes(env, cpu):
    e = env
    e.pod['gpus'] = 0
    for at in range(0, 20, 5):
        e.sample(at, cpu=cpu, gpu=None)
    assert not queued()
    e.sample(20, cpu=cpu, gpu=None)
    assert len(queued()) == (1 if cpu < .05 else 0)


@pytest.mark.parametrize('reset', ['busy', 'unreachable', 'gap'])
def test_event_rows_never_bridge_sampling_gaps_or_resets(env, reset):
    e = env
    e.sample(0)
    e.sample(5, gpu=1 if reset == 'busy' else 0, reachable=reset != 'unreachable')
    e.minute[0] = 15
    rh.append(e.root / 'PODS.jsonl', dict(utc=rh.now(), pod='guard', pod_id='abc123', lane='theory', event='irrelevant'))
    e.sample(25 if reset == 'gap' else 10)
    assert not queued()


def test_replacement_pod_does_not_inherit_evidence_or_throttle(env):
    e = env
    for at in (0, 5, 10):
        e.sample(at)
    assert len(queued()) == 1
    e.pod['pod_id'] = 'replacement'
    for at in (15, 20):
        e.sample(at)
        assert len(queued()) == 1
    e.sample(25)
    assert queued()[-1]['pod_id'] == 'replacement'
    assert queued()[-1]['minutes_idle'] == 10


def test_identityless_legacy_rows_never_count(env):
    e = env
    for at in (0, 5):
        e.minute[0] = at
        rh.append(e.root / 'PODS.jsonl', dict(utc=rh.now(), pod='guard', lane='theory', reachable=True, gpu_util=0))
    e.sample(10)
    assert not queued()


def test_new_idle_episode_alerts_after_recovery_without_old_throttle(env):
    for at in (0, 5, 10):
        env.sample(at)
    env.sample(15, gpu=100)
    for at in (20, 25, 30):
        env.sample(at)
    assert len(queued()) == 2
    assert queued()[-1]['minutes_idle'] == 10


def test_busy_but_stalled_progress_contract_alerts(env):
    e = env
    e.pod['progress_path'] = '/workspace/progress'
    e.sample(0, gpu=100, progress_path=e.pod['progress_path'],
             progress=dict(exists=True, mtime_ns=100, age_seconds=900))
    row = queued()[0]
    assert row['kind'] == 'pod-stalled' and row['minutes_stalled'] == 15
    assert json.loads((e.root / 'lanes/theory/alerts/pod-stalled-guard').read_text()) == row


def test_missing_progress_file_stalls_after_default_fifteen_minutes(env):
    e = env
    e.pod['progress_path'] = '/workspace/progress'
    for at in (0, 5, 10):
        e.sample(at, gpu=100, progress_path=e.pod['progress_path'], progress=dict(exists=False, mtime_ns=None))
    assert not queued()
    e.sample(15, gpu=100, progress_path=e.pod['progress_path'], progress=dict(exists=False, mtime_ns=None))
    assert queued()[0]['minutes_stalled'] == 15


def test_custom_progress_timeout_and_observed_mtime_change(env):
    e = env
    e.pod.update(progress_path='/workspace/progress', progress_stall_minutes=10)
    for at in (0, 5, 10):
        e.sample(at, gpu=100, progress_path=e.pod['progress_path'],
                 progress=dict(exists=True, mtime_ns=at + 1, age_seconds=0))
    assert not queued()
    for at in (15, 20):
        e.sample(at, gpu=100, progress_path=e.pod['progress_path'],
                 progress=dict(exists=True, mtime_ns=11, age_seconds=(at - 10) * 60))
    assert queued()[0]['minutes_stalled'] == 10


@pytest.mark.parametrize('bad', [{'error': 'unavailable'}, {}, {'exists': True, 'mtime_ns': 'bad'}])
def test_unavailable_progress_never_becomes_stall_evidence(env, bad):
    e = env
    e.pod.update(progress_path='/workspace/progress')
    for at in range(0, 31, 5):
        e.sample(at, gpu=100, progress_path=e.pod['progress_path'], progress=bad)
    assert not queued()


def test_no_alert_for_working_pod_with_advancing_progress(env):
    e = env
    e.pod.update(progress_path='/workspace/progress')
    for at in range(0, 61, 5):
        e.sample(at, gpu=80, progress_path=e.pod['progress_path'],
                 progress=dict(exists=True, mtime_ns=at + 1, age_seconds=0))
    assert not queued()


def test_failed_queue_append_retries_both_routes(env, monkeypatch):
    e = env
    queue = lane_alerts.queue_path()
    queue.mkdir()  # Opening it as a file fails, while the lane route succeeds.
    for at in (0, 5):
        e.sample(at)
    assert e.sample(10) == 1
    assert (e.root / 'lanes/theory/alerts/pod-idle-guard').is_file()
    monkeypatch.setenv('RH_AGENT_ALERTS', str(queue / 'retry.jsonl'))
    assert e.sample(15) == 0
    assert len(queued()) == 1
    assert queued()[0]['minutes_idle'] == 15


@pytest.mark.parametrize('script,expected', [
    ('apt-get install foo\n', True),
    ('apt-get -y -q install foo\n', True),
    ('export DEBIAN_FRONTEND=noninteractive\napt-get install foo\n', True),
    ('export DEBIAN_FRONTEND=noninteractive\napt-get -y -q install foo\n', False),
    ('DEBIAN_FRONTEND=noninteractive apt-get install -y -q foo\n', False),
    ('export DEBIAN_FRONTEND=noninteractive\n/usr/bin/apt-get --yes install foo\n', False),
    ('export DEBIAN_FRONTEND=noninteractive\napt-get -qy install foo\n', False),
    ('# export DEBIAN_FRONTEND=noninteractive\napt-get install foo # -y\n', True),
    ('export DEBIAN_FRONTEND=noninteractive; apt-get install foo; apt-get -y install bar\n', True),
    ('export DEBIAN_FRONTEND=noninteractive\napt-get install foo # unsafe\napt-get -y install bar\n', True),
    ('export DEBIAN_FRONTEND=noninteractive\napt-get -q \\\n -y install foo\n', False),
    ('export DEBIAN_FRONTEND=noninteractive\nunset DEBIAN_FRONTEND\napt-get -y install foo\n', True),
])
def test_setup_script_lint_positive_and_negative(tmp_path, script, expected):
    setup = tmp_path / 'setup.sh'
    setup.write_text(script)
    assert bool(pod_guard.setup_warnings(dict(name='test', setup_script=str(setup)))) == expected


def test_gpu_progress_and_unreadable_setup_are_lint_warnings(env, monkeypatch):
    e = env
    e.pod['setup_script'] = str(e.root / 'missing.sh')
    (e.root / 'pods.json').write_text(json.dumps([e.pod]))
    monkeypatch.setattr(harvest, 'lint', lambda *a, **kw: [])
    warnings = []
    assert pods.lint(e.root / 'lanes/theory', rh, warnings=warnings) == []
    assert len(warnings) == 2
    assert 'no progress_path' in warnings[0] and 'cannot be read' in warnings[1]
    e.pod['progress_path'] = '/workspace/progress'
    assert not any('no progress_path' in w for w in pod_guard.warnings([e.pod]))


@pytest.mark.parametrize('key,value', [('progress_stall_minutes', 0), ('progress_stall_minutes', -1),
    ('progress_stall_minutes', float('nan')), ('progress_path', '/a/../b'), ('progress_path', 'relative'),
    ('setup_script', '/a\nb')])
def test_invalid_contract_rejected_without_probing(env, key, value):
    e = env
    e.pod[key] = value
    (e.root / 'pods.json').write_text(json.dumps([e.pod]))
    with pytest.raises(ValueError):
        pods.registry(e.root)
    e.forbidden.assert_not_called()


def test_cli_set_contract_preserves_other_pods_and_fields_without_ssh(env):
    e = env
    other = dict(e.pod, name='other', pod_id='other', ssh_target=('other-1' + chr(64) + 'ssh.runpod.io'))
    original = [dict(e.pod, keep=True, custom='preserve'), other]
    before = json.dumps(original)
    (e.root / 'pods.json').write_text(before)
    assert rh.main(['--root', str(e.root), 'pods', 'set', 'guard', '--progress-path', '/workspace/progress',
                    '--progress-stall-minutes', '12', '--setup-script', str(e.root / 'setup.sh')]) == 0
    saved = pods.registry(e.root)
    assert saved[0]['progress_stall_minutes'] == 12 and saved[0]['keep'] is True
    assert saved[0]['custom'] == 'preserve' and saved[1] == other
    assert (e.root / 'pods.json.bak-ownership').read_text() == before
    e.forbidden.assert_not_called()


def test_cli_add_accepts_contract_in_its_single_inventory_probe(env, monkeypatch):
    e = env
    (e.root / 'pods.json').write_text('[]')
    probe = Mock(return_value=dict(vcpus=16, gpus=1))
    monkeypatch.setattr(pods, 'probe', probe)
    assert rh.main(['--root', str(e.root), 'pods', 'add', 'guard', '--pod-id', 'abc123',
        '--ssh-target', ('abc123-1' + chr(64) + 'ssh.runpod.io'), '--lane', 'theory', '--dollars-per-hour', '5.29',
        '--owner-note', 'test', '--results', '/workspace/results', '--harvest-dest', e.pod['harvest_dest'],
        '--progress-path', '/workspace/progress', '--progress-stall-minutes', '12',
        '--setup-script', str(e.root / 'setup.sh')]) == 0
    assert probe.call_count == 1
    assert pods.registry(e.root)[0]['progress_path'] == '/workspace/progress'


def test_probe_collects_progress_with_metrics_in_one_remote_read(env, monkeypatch):
    e = env
    e.pod['progress_path'] = '/workspace/progress'
    fields = dict(NPROC='16', MAX='max 100000', GPUS='GPU 0: H200', GPUUTIL='100',
        CPU1='usage_usec 1000000', CPU2='usage_usec 81000000', TIME1='10 0', TIME2='15 0',
        PROGRESS=json.dumps(dict(exists=True, mtime_ns=100, age_seconds=900)))
    remote = Mock(return_value=fields)
    monkeypatch.setattr(pods, 'remote_read', remote)
    measured = ORIGINAL_PROBE(e.pod)
    assert measured['progress']['age_seconds'] == 900 and measured['cpu_frac'] == 1
    remote.assert_called_once_with(e.pod, timeout=60)


ORIGINAL_PROBE = pods.probe


def test_progress_command_stats_quoted_path_without_reading_or_changing_it(tmp_path):
    progress = tmp_path / "progress' $(touch injection) ; file"
    progress.write_text('SECRET scientific data must never be read or returned')
    os.utime(progress, (time.time() - 1000, time.time() - 1000))
    before = progress.stat().st_mtime_ns
    command = pod_guard.progress_command(dict(progress_path=str(progress)))
    result = subprocess.run(shlex.split(command), text=True, capture_output=True, check=True)
    observed = json.loads(result.stdout)
    assert observed['mtime_ns'] == before and observed['age_seconds'] >= 999
    assert 'SECRET' not in result.stdout + result.stderr
    assert progress.stat().st_mtime_ns == before
    assert not (tmp_path / 'injection').exists()


def test_underused_window_handles_five_minute_cadence_and_sampling_gaps(env):
    e = env
    for at in range(0, 26, 5):
        e.sample(at, cpu=.3, gpu=30)
    history = rh.rows(e.root / 'PODS.jsonl')
    assert pods.state(history, e.pod) == (True, False)
    sparse = [history[0], history[-1]]
    assert pods.state(sparse, e.pod) == (False, False)


def test_setup_wrapper_environment_and_group_timeout_are_enforced(tmp_path):
    helper = Path(__file__).resolve().parents[1] / 'pod/setup.sh'
    # Stand-ins record arguments; the wrapper remains a real bash process.
    tools_dir = tmp_path / 'bin'
    tools_dir.mkdir()
    capture = tmp_path / 'capture.json'
    recorder = tools_dir / 'setsid'
    recorder.write_text('#!' + sys.executable + '\nimport json, os, sys\n'
        'open(os.environ["CAPTURE"], "w").write(json.dumps([sys.argv[1:], os.environ.get("DEBIAN_FRONTEND")]))\n')
    recorder.chmod(0o755)
    environment = dict(os.environ, PATH=str(tools_dir) + os.pathsep + os.environ['PATH'], CAPTURE=str(capture))
    subprocess.run(['bash', str(helper), '/workspace/setup.sh'], env=environment, check=True)
    argv, frontend = json.loads(capture.read_text())
    assert frontend == 'noninteractive'
    assert argv == ['timeout', '--kill-after=30s', '15m', 'bash', '-euo', 'pipefail', '/workspace/setup.sh']


def test_fifty_minute_harvest_does_not_starve_five_minute_samples_or_other_harvests(env, monkeypatch):
    e = env
    other = dict(e.pod, name='short', pod_id='short', ssh_target=('short-1' + chr(64) + 'ssh.runpod.io'))
    (e.root / 'pods.json').write_text(json.dumps([e.pod, other]))
    release, copying = threading.Event(), threading.Event()
    short_done = threading.Event()
    starts, workers = [], []
    guard = threading.Lock()
    def copy(root, entries, clock, name):
        with guard:
            starts.append((name, e.minute[0]))
        if name == 'guard':
            copying.set()
            assert release.wait(5), 'virtual 50-minute harvest was never released'
        return 0
    def reap(*a, **kw):
        short_done.set()
        return 0
    monkeypatch.setattr(harvest, 'run', copy)
    monkeypatch.setattr(pods, 'reap', reap)
    monkeypatch.setattr(rh, 'lint_monitor', lambda *a: 0)
    monkeypatch.setattr(pods.cadence, 'monitor', lambda *a: 0)
    monkeypatch.setattr(pods, 'probe', lambda p: dict(vcpus=16, gpus=1, cpu_frac=1, gpu_util=100))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        def spawn(argv, **kwargs):
            assert kwargs['start_new_session'] is True
            workers.append(pool.submit(pod_maintenance.run, e.root, argv[-3], argv[-1], rh, pods))
        monkeypatch.setattr(subprocess, 'Popen', spawn)
        try:
            for at in range(0, 51, 5):
                e.minute[0] = at
                if at % 15 == 0:
                    short_done.clear()
                    assert rh.main(['--root', str(e.root), 'pods', 'check']) == 0
                    assert copying.wait(2)
                    assert short_done.wait(2)
                    # Wait until the short worker has released its lock.
                    for future in workers[1:]:
                        future.result(timeout=2)
                assert rh.main(['--root', str(e.root), 'pods', 'sample']) == 0
                assert not release.is_set()
            assert sorted(starts) == [('guard', 0), ('short', 0), ('short', 15), ('short', 30), ('short', 45)]
            rows = rh.rows(e.root / 'PODS.jsonl')
            assert len(rows) == 22
            for name in ('guard', 'short'):
                observations = [pod_guard.timestamp(r['utc']) for r in rows if r['pod'] == name]
                assert all((b - a).total_seconds() == 300 for a, b in zip(observations, observations[1:]))
        finally:
            release.set()
        for future in workers:
            future.result(timeout=2)


def test_sampler_ignores_registry_maintenance_and_harvest_locks(env, monkeypatch):
    e = env
    monkeypatch.setattr(pods, 'probe', lambda p: dict(vcpus=16, gpus=1, cpu_frac=1, gpu_util=100))
    with (e.root / '.pods.lock').open('a') as registry_lock:
        fcntl.flock(registry_lock, fcntl.LOCK_EX)
        assert rh.main(['--root', str(e.root), 'pods', 'sample']) == 0
    assert len(rh.rows(e.root / 'PODS.jsonl')) == 1
    e.forbidden.assert_not_called()


def test_maintenance_skips_replaced_identity_without_harvest(env, monkeypatch):
    copy = Mock(side_effect=AssertionError('no harvest of replacement'))
    monkeypatch.setattr(harvest, 'run', copy)
    assert pod_maintenance.run(env.root, 'guard', 'oldidentity', rh, pods) == 0
    copy.assert_not_called()
    env.forbidden.assert_not_called()


def test_cancelled_harvest_never_starts_reaper(env, monkeypatch):
    monkeypatch.setattr(harvest, 'run', Mock(side_effect=KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        pod_maintenance.run(env.root, 'guard', 'abc123', rh, pods)
    env.forbidden.assert_not_called()
