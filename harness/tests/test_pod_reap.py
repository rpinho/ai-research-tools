"""Idle reaping safety: isolated registries and real local copy protocol, never RunPod."""
import datetime as dt
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock
import urllib.request

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harvest
import pods
import rh


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('provider/real termination forbidden in tests')
    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    monkeypatch.setattr(pods, 'terminate_self', forbidden)


@pytest.fixture
def env(tmp_path, monkeypatch):
    now = [dt.datetime(2026, 11, 3, 6, tzinfo=dt.timezone.utc)]
    pod = dict(name='empty', pod_id='abc123', lane='team', vcpus=32, gpus=0,
               dollars_per_hour=.96, owner_note='fixture', ssh_target=('abc123-1' + chr(64) + 'ssh.runpod.io'),
               results=['/workspace/result'], harvest_dest=str(tmp_path / 'results'), work_queue='harness')
    (tmp_path / 'pods.json').write_text(json.dumps([pod]))
    (tmp_path / pods.QUEUE).write_text('[]')
    (tmp_path / 'pod-gate.json').write_text('{"reap":"enforce"}')
    metric = dict(vcpus=32, gpus=0, cpu_frac=.01, gpu_util=None)
    probe = Mock(side_effect=lambda _: dict(metric))
    order = []
    def connect(root, p, dest, mode='dispatch', expected_generation=None):
        order.append(mode)
        if mode == 'inspect':
            return dict(running=0, pending=0)
        assert mode == 'reap'
        logged = harvest.rows(harvest.state_root(root) / 'HARVEST.jsonl')
        assert logged[-1]['verified'] is True
        assert logged[-1]['reason'].startswith('reap-final-')
        return 0
    dispatch = Mock(side_effect=connect)
    def copy(root, p, clock, reason):
        order.append('harvest')
        row = dict(utc=clock.now(), pod=p['name'], pod_id=p['pod_id'], lane=p['lane'],
                   scope=harvest.scope(p), reason=reason, verified=True)
        harvest.append(harvest.state_root(root) / 'HARVEST.jsonl', row)
        return row
    final = Mock(side_effect=copy)
    push = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(pods, 'probe', probe)
    monkeypatch.setattr(pods, 'dispatch', dispatch)
    monkeypatch.setattr(harvest, 'harvest_one', final)
    monkeypatch.setattr(pods.subprocess, 'run', push)
    clock = SimpleNamespace(now=lambda: now[0].isoformat())
    return SimpleNamespace(root=tmp_path, pod=pod, now=now, clock=clock, metric=metric,
                           probe=probe, dispatch=dispatch, final=final, push=push, order=order)


def advance(e, minutes):
    e.now[0] += dt.timedelta(minutes=minutes)


def observe(e, dry_run=False):
    return pods.reap(e.root, [e.pod], e.clock, dry_run=dry_run)


def idle_window(e):
    assert observe(e) == 0
    advance(e, 15)
    assert observe(e) == 0
    advance(e, 15)


def shutdown_calls(e):
    return [c for c in e.dispatch.call_args_list if c.kwargs.get('mode') == 'reap']


@pytest.mark.parametrize('gpu', [False, True])
def test_idle_verified_reaps_and_harvest_is_logged_first(env, gpu):
    e = env
    if gpu:
        e.pod['gpus'] = e.metric['gpus'] = 1
        e.metric.update(cpu_frac=.8, gpu_util=0)  # GPU criterion is exact zero.
        (e.root / 'pods.json').write_text(json.dumps([e.pod]))
    idle_window(e)
    assert observe(e) == 0
    assert len(shutdown_calls(e)) == 1
    assert e.order[-4:] == ['inspect', 'harvest', 'inspect', 'reap']
    assert pods.registry(e.root) == []
    assert (e.root / 'pods.json.bak-reap').exists()
    assert (e.root / 'alerts/pod-reaped-empty').read_text() == 'empty\n'
    assert e.push.call_args.args[0][1:] == ['empty', 'Pod reaped']


@pytest.mark.parametrize('failure', ['unverified', 'not-logged', 'exception', 'wrong-scope', 'old-row'])
def test_failed_final_harvest_alerts_and_never_terminates(env, failure):
    e = env
    idle_window(e)
    def bad_copy(root, pod, clock, reason):
        if failure == 'exception':
            raise OSError('SECRET')
        row = dict(utc=clock.now(), pod=pod['name'], pod_id=pod['pod_id'], lane=pod['lane'],
                   scope=harvest.scope(pod), reason=reason, verified=True)
        if failure == 'unverified':
            row['verified'] = False
        if failure == 'wrong-scope':
            row['scope'] = 'other'
        if failure == 'old-row':
            row['utc'] = '2026-11-02T01:00:00Z'
        if failure != 'not-logged':
            harvest.append(harvest.state_root(root) / 'HARVEST.jsonl', row)
        return row
    e.final.side_effect = bad_copy
    assert observe(e) == 1
    assert not shutdown_calls(e)
    assert len(pods.registry(e.root)) == 1
    assert (e.root / 'alerts/pod-reap-failed-empty').read_text() == 'empty\n'
    assert 'SECRET' not in (e.root / 'POD_REAP.jsonl').read_text()


@pytest.mark.parametrize('busy', ['cpu', 'gpu', 'running', 'pending', 'unreachable', 'resources'])
def test_busy_or_unknown_resets_window(env, busy):
    e = env
    observe(e)
    advance(e, 15)
    if busy == 'cpu':
        e.metric['cpu_frac'] = .10
    elif busy == 'gpu':
        e.pod['gpus'] = e.metric['gpus'] = 1
        e.metric['gpu_util'] = 1
    elif busy in ('running', 'pending'):
        e.dispatch.side_effect = lambda *a, **kw: dict(running=int(busy == 'running'), pending=int(busy == 'pending'))
    elif busy == 'unreachable':
        e.probe.side_effect = OSError('SECRET')
    else:
        e.metric['vcpus'] = 16
    observe(e)
    advance(e, 15)
    observe(e)
    assert not shutdown_calls(e)
    e.final.assert_not_called()


@pytest.mark.parametrize('protected', ['keep', 'restricted-a', 'restricted-b'])
def test_protected_and_unregistered_queue_never_connect(env, protected):
    e = env
    if protected == 'keep':
        e.pod['keep'] = True
    else:
        e.pod['name'] = protected
    idle_window(e)
    observe(e)
    e.probe.assert_not_called()
    e.dispatch.assert_not_called()
    e.final.assert_not_called()


@pytest.mark.parametrize('target', [dict(), dict(runnable=False), dict(owner='legacy'), dict(depends_on=['absent'])])
def test_queue_items_without_process_alert_stuck_and_never_reap(env, target):
    e = env
    item = dict(id='job', lane='team', argv=['true'], runnable=True)
    item.update(target)
    (e.root / pods.QUEUE).write_text(json.dumps([item]))
    idle_window(e)
    observe(e)
    assert (e.root / 'alerts/pod-stuck-empty').read_text() == 'empty\n'
    e.final.assert_not_called()
    assert not shutdown_calls(e)


def test_orphan_claim_without_queue_item_is_stuck(env):
    e = env
    (e.root / pods.CLAIMS).write_text(json.dumps({'removed': dict(owner='abc123', status='claimed')}))
    assert observe(e) == 0
    assert (e.root / 'alerts/pod-stuck-empty').exists()
    e.final.assert_not_called()


def test_missing_queue_file_is_unknown_and_not_empty(env):
    e = env
    # Rename only this fixture's file, rather than deleting any existing queue.
    (e.root / pods.QUEUE).rename(e.root / 'absent-queue')
    assert observe(e) == 1
    assert (e.root / 'alerts/pod-reap-unknown-empty').exists()
    e.final.assert_not_called()
    e.dispatch.assert_not_called()


@pytest.mark.parametrize('gap', [17, 60, 1440])
def test_missing_observations_never_count_as_idle(env, gap):
    e = env
    observe(e)
    advance(e, gap)
    observe(e)
    e.final.assert_not_called()


def test_new_work_during_final_harvest_prevents_shutdown(env):
    e = env
    idle_window(e)
    copy = e.final.side_effect
    def new_work(*a, **kw):
        result = copy(*a, **kw)
        (e.root / pods.QUEUE).write_text(json.dumps([dict(id='new', lane='team', argv=['true'])]))
        return result
    e.final.side_effect = new_work
    assert observe(e) == 0
    assert not shutdown_calls(e)
    assert len(pods.registry(e.root)) == 1


def test_work_completed_between_samples_resets_idle_window(env):
    e = env
    idle_window(e)
    (e.root / pods.QUEUE).write_text(json.dumps([dict(id='new', lane='team', argv=['true'])]))
    (e.root / pods.CLAIMS).write_text(json.dumps({'new': dict(owner='abc123', status='done')}))
    observe(e)
    e.final.assert_not_called()
    advance(e, 15)
    observe(e)
    e.final.assert_not_called()
    advance(e, 15)
    observe(e)
    assert len(shutdown_calls(e)) == 1


def test_keep_added_during_final_harvest_prevents_shutdown(env):
    e = env
    idle_window(e)
    copy = e.final.side_effect
    def keep(*a, **kw):
        result = copy(*a, **kw)
        (e.root / 'pods.json').write_text(json.dumps([dict(e.pod, keep=True)]))
        return result
    e.final.side_effect = keep
    assert observe(e) == 0
    assert not shutdown_calls(e)
    assert pods.registry(e.root)[0]['keep'] is True


def test_shutdown_failure_preserves_registry_and_alerts(env):
    e = env
    idle_window(e)
    connect = e.dispatch.side_effect
    def failed(*a, **kw):
        if kw.get('mode') == 'reap':
            raise ValueError('SECRET copy failure')
        return connect(*a, **kw)
    e.dispatch.side_effect = failed
    assert observe(e) == 1
    assert len(pods.registry(e.root)) == 1
    assert (e.root / 'alerts/pod-reap-failed-empty').exists()


def test_explicit_dry_run_overrides_enforce_and_still_verifies_final_harvest(env):
    e = env
    idle_window(e)
    assert observe(e, dry_run=True) == 0
    e.final.assert_called_once()
    assert not shutdown_calls(e)
    assert len(pods.registry(e.root)) == 1
    assert harvest.rows(e.root / 'POD_REAP.jsonl')[-1]['event'] == 'WOULD_REAP'


def test_rollout_defaults_24h_then_enforces_and_explicit_modes_win(tmp_path):
    start = dt.datetime(2026, 11, 3, tzinfo=dt.timezone.utc)
    assert pods.reap_mode(tmp_path, start.isoformat()) == 'dry-run'
    assert pods.reap_mode(tmp_path, (start + dt.timedelta(hours=23, minutes=59)).isoformat()) == 'dry-run'
    after = (start + dt.timedelta(hours=24)).isoformat()
    assert pods.reap_mode(tmp_path, after) == 'enforce'
    (tmp_path / 'pod-gate.json').write_text('{"reap":"dry-run"}')
    assert pods.reap_mode(tmp_path, after) == 'dry-run'
    (tmp_path / 'pod-gate.json').write_text('{"reap":"enforce"}')
    assert pods.reap_mode(tmp_path, start.isoformat()) == 'enforce'
    assert pods.reap_mode(tmp_path, after, dry_run=True) == 'dry-run'


@pytest.mark.parametrize('config', ['{', '[]', '{"reap":"bad"}', 'null'])
def test_broken_config_is_dry_run(tmp_path, config):
    (tmp_path / 'pod-gate.json').write_text(config)
    assert pods.reap_mode(tmp_path, '2026-11-03T00:00:00Z') == 'dry-run'


def test_check_runs_reaper_even_when_periodic_harvest_failed(env, monkeypatch):
    e = env
    order = []
    monkeypatch.setattr(pods, 'check', lambda *a: order.append('check') or 0)
    monkeypatch.setattr(harvest, 'run', lambda *a: order.append('periodic-harvest') or 1)
    monkeypatch.setattr(pods, 'reap', lambda *a, **k: order.append('reap') or 0)
    assert rh.main(['--root', str(e.root), 'pods', 'check']) == 1
    assert order == ['check', 'periodic-harvest', 'reap']


def test_push_failure_retries_and_pushes_only_name(env):
    e = env
    e.push.return_value = SimpleNamespace(returncode=1)
    pods.reap_alert(e.root, e.pod, 'stuck')
    e.push.return_value = SimpleNamespace(returncode=0)
    pods.reap_alert(e.root, e.pod, 'stuck')
    pods.reap_alert(e.root, e.pod, 'stuck')
    assert e.push.call_count == 2
    for call in e.push.call_args_list:
        assert call.args[0][1:] == ['empty', 'Pod stuck']


def test_reaped_push_failure_retries_after_registry_row_removed(env):
    e = env
    idle_window(e)
    e.push.return_value = SimpleNamespace(returncode=1)
    assert observe(e) == 0
    assert pods.registry(e.root) == []
    assert e.push.call_count == 1
    e.push.return_value = SimpleNamespace(returncode=0)
    assert pods.reap(e.root, [], e.clock) == 0
    assert e.push.call_count == 2
    assert pods.reap(e.root, [], e.clock) == 0
    assert e.push.call_count == 2


def test_real_copy_protocol_before_fake_termination(tmp_path):
    """Same shared shutdown method used by runner_main's reap branch."""
    work = tmp_path / 'workspace'
    work.mkdir()
    (work / 'result').write_bytes(b'science' * 20000)
    (tmp_path / pods.QUEUE).write_text('[]')
    events, failures = [], []
    runner = pods.SlotRunner(work, 32, terminate=lambda: events.append('terminated'))
    pod = dict(name='fake', pod_id='fake', lane='team', vcpus=32, gpus=0)
    a, b = socket.socketpair()
    a.settimeout(5)
    b.settimeout(5)
    with a, b, a.makefile('rwb') as ps, b.makefile('rwb') as ms:
        pc, mc = pods.JsonChannel(ps, ps), pods.JsonChannel(ms, ms)
        def controller():
            try:
                result = pods.serve_controller(mc, pods.WorkQueue(tmp_path, 'team', 'fake', 32, 0),
                    tmp_path / 'saved', pod, notify=Mock(), mode='reap')
                events.append(('ack', result))
            except Exception as error:
                failures.append(error)
        thread = threading.Thread(target=controller)
        thread.start()
        pc.send(dict(op='hello', lane='team', owner='fake', vcpus=32, gpus=0,
                     capabilities=['reap-v1'], running=0, pending=0))
        assert pc.receive() == dict(ok=True, mode='reap')
        runner.verified_shutdown(pods.PodQueue(pc, work))
        assert runner.ended
        pc.send(dict(op='ended', verified_shutdown=True))
        thread.join(5)
        assert not thread.is_alive() and not failures
    assert events == ['terminated', ('ack', 0)]
    assert pods.workspace_manifest(next((tmp_path / 'saved').iterdir())) == pods.workspace_manifest(work)


@pytest.mark.parametrize('failure', ['copy', 'terminate', 'running', 'pending'])
def test_shared_shutdown_failure_never_claims_success(tmp_path, failure):
    terminate = Mock(side_effect=ValueError('failed') if failure == 'terminate' else None)
    runner = pods.SlotRunner(tmp_path / 'workspace', 32, terminate=terminate)
    queue = SimpleNamespace(copy=Mock(), alert=Mock())
    if failure == 'copy':
        queue.copy.side_effect = OSError('disk full')
    if failure == 'running':
        runner.active['token'] = ({}, Mock(poll=Mock(return_value=None)), None)
    if failure == 'pending':
        runner.completed.append(({}, 0))
    if failure in ('running', 'pending'):
        with pytest.raises(ValueError):
            runner.verified_shutdown(queue)
        queue.copy.assert_not_called()
    else:
        runner.verified_shutdown(queue)
        queue.alert.assert_called_once()
    if failure != 'terminate':
        terminate.assert_not_called()
    assert not runner.ended


@pytest.mark.parametrize('old_or_busy', ['old', 'running', 'pending', 'no-copy'])
def test_controller_refuses_old_busy_or_unverified_shutdown(tmp_path, old_or_busy):
    queue = pods.WorkQueue(tmp_path, 'team', 'fake', 32, 0)
    hello = dict(op='hello', lane='team', owner='fake', vcpus=32, gpus=0,
                 capabilities=['reap-v1'], running=0, pending=0)
    if old_or_busy == 'old':
        hello.pop('capabilities')
    if old_or_busy in ('running', 'pending'):
        hello[old_or_busy] = 1
    channel = SimpleNamespace(receive=Mock(side_effect=[hello, dict(op='ended', verified_shutdown=True)]), send=Mock())
    with pytest.raises(ValueError):
        pods.serve_controller(channel, queue, tmp_path / 'saved', dict(pod_id='fake', name='fake'),
                              notify=Mock(), mode='reap')
    assert not (tmp_path / 'saved').exists()


def test_timed_channel_partial_reads_and_deadline():
    a, b = socket.socketpair()
    with a, b, a.makefile('rwb', buffering=0) as reader:
        channel = pods.JsonChannel(reader, reader, deadline=time.monotonic() + 1)
        b.sendall(b'{"a":1}\n{"b":2}\n')
        assert channel.receive() == {'a': 1}
        assert channel.receive() == {'b': 2}
        channel.deadline = time.monotonic() - 1
        with pytest.raises(TimeoutError):
            channel.receive()


def test_production_idle_loop_cannot_bypass_reaper_gate(tmp_path):
    clock = [0]
    terminated = Mock()
    runner = pods.SlotRunner(tmp_path / 'workspace', 32, terminate=terminated,
                            clock=lambda: clock[0], auto_shutdown=False)
    queue = SimpleNamespace(claim=Mock(return_value=None), copy=Mock(), alert=Mock())
    runner.tick(queue)
    clock[0] = 86400
    runner.tick(queue)
    queue.copy.assert_not_called()
    terminated.assert_not_called()


def test_production_controller_denies_old_autonomous_copy(tmp_path):
    channel = SimpleNamespace(receive=Mock(side_effect=[dict(op='copy', manifest={}), dict(op='ended')]), send=Mock())
    assert pods.serve_controller(channel, pods.WorkQueue(tmp_path, 'team', 'fake', 32, 0),
        tmp_path / 'saved', dict(name='fake', pod_id='fake'), notify=Mock(), require_reaper=True) == 0
    assert channel.send.call_args.args[0]['error']
    assert not (tmp_path / 'saved').exists()


def test_keep_added_during_full_workspace_copy_denies_shutdown(env, monkeypatch):
    e = env
    manifest = dict(files={}, directories=[], bytes=0)
    def copied(*args):
        (e.root / 'pods.json').write_text(json.dumps([dict(e.pod, keep=True)]))
        return manifest
    monkeypatch.setattr(pods, 'copy_results', copied)
    hello = dict(op='hello', lane='team', owner=e.pod['pod_id'], vcpus=32, gpus=0,
                 capabilities=['reap-v1'], running=0, pending=0)
    channel = SimpleNamespace(receive=Mock(side_effect=[hello, dict(op='copy', manifest=manifest),
        dict(op='alert', message='retained')]), send=Mock())
    with pytest.raises(ValueError):
        pods.serve_controller(channel, pods.WorkQueue(e.root, 'team', e.pod['pod_id'], 32, 0),
            e.root / 'saved', e.pod, notify=Mock(return_value=True), mode='reap', require_reaper=True)
    assert any(c.args[0].get('op') == 'copied' and c.args[0].get('error') for c in channel.send.call_args_list)
    assert not any(c.args[0].get('op') == 'copied' and 'manifest' in c.args[0] for c in channel.send.call_args_list)


@pytest.mark.parametrize('copy_failure', [False, True])
def test_real_runner_inspection_then_reap_with_fake_termination(tmp_path, copy_failure):
    """Real daemon, socket, full byte/hash transfer, pod-local fake command and ack."""
    root = Path(pods.__file__).parent
    work = tmp_path / 'workspace'
    work.mkdir()
    (work / 'result.txt').write_bytes(b'result' * 20000)
    if copy_failure:
        (work / 'unsafe-link').symlink_to(work / 'result.txt')
    marker = tmp_path / 'fake-terminated'
    command = [sys.executable, '-c', 'from pathlib import Path; Path(' + repr(str(marker)) + ').touch()']
    with tempfile.TemporaryDirectory(prefix='rh-reap-', dir='/tmp') as socket_dir:
        socket_path = Path(socket_dir) / 'runner.sock'
        daemon = subprocess.Popen([sys.executable, str(root / 'pod/runner.py'), 'serve',
            '--socket', str(socket_path), '--workspace', str(work), '--lane', 'team',
            '--owner', 'fake', '--vcpus', '32', '--gpus', '0', '--terminate-json', json.dumps(command)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        primary, primary_stream, thread = None, None, None
        failures, pulling = [], threading.Event()
        try:
            deadline = time.monotonic() + 5
            while not socket_path.exists() and time.monotonic() < deadline and daemon.poll() is None:
                time.sleep(.01)
            assert socket_path.exists()
            queue = pods.WorkQueue(tmp_path, 'team', 'fake', 32, 0)
            pod = dict(name='fake', pod_id='fake')
            claim = queue.claim
            def pull(*args):
                result = claim(*args)
                pulling.set()
                return result
            queue.claim = pull
            primary = socket.socket(socket.AF_UNIX)
            primary.connect(str(socket_path))
            primary.settimeout(5)
            primary_stream = primary.makefile('rwb')
            def dispatch_work():
                try:
                    pods.serve_controller(pods.JsonChannel(primary_stream, primary_stream), queue,
                        tmp_path / 'ordinary-copy', pod, notify=Mock(), require_reaper=True)
                except (EOFError, OSError, ValueError):
                    pass  # deliberate local daemon shutdown/disconnect
                except Exception as error:
                    failures.append(error)
            thread = threading.Thread(target=dispatch_work)
            thread.start()
            assert pulling.wait(5)
            # Match production: dispatcher remains connected while the reaper
            # holds the queue lock. Inspection/copy must not wait on its claim RPC.
            with pods.work_lock(tmp_path):
                with socket.socket(socket.AF_UNIX) as client:
                    client.connect(str(socket_path))
                    client.settimeout(5)
                    with client.makefile('rwb') as stream:
                        status = pods.serve_controller(pods.JsonChannel(stream, stream), queue, None, pod,
                                                       notify=Mock(), mode='inspect')
                        assert status == dict(running=0, pending=0)
                assert not marker.exists()
                with socket.socket(socket.AF_UNIX) as client:
                    client.connect(str(socket_path))
                    client.settimeout(5)
                    with client.makefile('rwb') as stream:
                        if copy_failure:
                            with pytest.raises((EOFError, ValueError)):
                                pods.serve_controller(pods.JsonChannel(stream, stream), queue, tmp_path / 'saved', pod,
                                                      notify=Mock(), mode='reap')
                            assert not marker.exists() and daemon.poll() is None
                        else:
                            assert pods.serve_controller(pods.JsonChannel(stream, stream), queue, tmp_path / 'saved', pod,
                                                         notify=Mock(), mode='reap') == 0
                            assert marker.exists()
                            assert pods.workspace_manifest(next((tmp_path / 'saved').iterdir())) == pods.workspace_manifest(work)
            if not copy_failure:
                assert daemon.wait(timeout=5) == 0
        finally:
            if daemon.poll() is None:
                daemon.terminate()  # only this test's newly created local daemon
            daemon.wait(timeout=5)
            if primary:
                try:
                    primary.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass  # daemon already closed this test's Unix socket
            if thread:
                thread.join(5)
                assert not thread.is_alive() and not failures
            if primary_stream:
                primary_stream.close()
            if primary:
                primary.close()
            daemon.stdout.close()
            daemon.stderr.close()


@pytest.fixture
def done_env(env):
    e = env
    e.pod['work_queue'] = 'done-file:/workspace/HARNESS_DONE'
    (e.root / 'pods.json').write_text(json.dumps([e.pod]))
    # The done-file mode has no dependency on the harness work queue.
    (e.root / pods.QUEUE).rename(e.root / 'unused-harness-queue')
    e.metric['done_file'] = dict(exists=True, empty=True, generation='done-and-results-v1')
    return e


@pytest.mark.parametrize('gpu', [False, True])
def test_done_file_idle_uses_shared_harvest_and_shutdown(done_env, gpu):
    e = done_env
    if gpu:
        e.pod['gpus'] = e.metric['gpus'] = 2
        e.metric.update(gpu_util=0, cpu_frac=.8)
        (e.root / 'pods.json').write_text(json.dumps([e.pod]))
    idle_window(e)
    assert observe(e) == 0
    assert len(shutdown_calls(e)) == 1
    assert e.order[-4:] == ['inspect', 'harvest', 'inspect', 'reap']
    assert pods.registry(e.root) == []


@pytest.mark.parametrize('condition', ['missing', 'results-changing', 'cpu-busy', 'gpu-busy', 'malformed'])
def test_done_file_blocked_never_harvests_or_reaps(done_env, condition):
    e = done_env
    if condition == 'missing':
        e.metric['done_file'] = dict(exists=False, empty=False, generation='missing')
    elif condition == 'results-changing':
        e.metric['done_file']['empty'] = False
    elif condition == 'malformed':
        e.metric['done_file'] = dict(exists=True, empty=True)
    elif condition == 'cpu-busy':
        e.metric['cpu_frac'] = .10
    else:
        e.pod['gpus'] = e.metric['gpus'] = 2
        e.metric['gpu_util'] = 1
    for _ in range(5):
        observe(e)
        advance(e, 15)
    e.final.assert_not_called()
    assert not shutdown_calls(e)
    assert not (e.root / 'alerts/pod-stuck-empty').exists()


def test_done_file_removal_resets_full_window(done_env):
    e = done_env
    observe(e)
    advance(e, 15)
    e.metric['done_file'] = dict(exists=False, empty=False, generation='missing')
    observe(e)
    advance(e, 15)
    e.metric['done_file'] = dict(exists=True, empty=True, generation='new-marker')
    observe(e)
    advance(e, 15)
    observe(e)
    e.final.assert_not_called()
    advance(e, 15)
    observe(e)
    assert len(shutdown_calls(e)) == 1


@pytest.mark.parametrize('change', ['marker', 'result', 'missing'])
def test_done_file_change_during_final_harvest_prevents_shutdown(done_env, change):
    e = done_env
    idle_window(e)
    copy = e.final.side_effect
    def changed(*args, **kwargs):
        result = copy(*args, **kwargs)
        e.metric['done_file']['generation'] = 'changed-' + change
        if change == 'missing':
            e.metric['done_file'].update(exists=False, empty=False)
        return result
    e.final.side_effect = changed
    assert observe(e) == 0
    assert not shutdown_calls(e)
    assert len(pods.registry(e.root)) == 1


def test_done_file_dry_run_reuses_final_harvest_without_shutdown(done_env):
    e = done_env
    idle_window(e)
    assert observe(e, dry_run=True) == 0
    e.final.assert_called_once()
    assert not shutdown_calls(e)
    assert harvest.rows(e.root / 'POD_REAP.jsonl')[-1]['event'] == 'WOULD_REAP'


def test_unknown_owner_alert_after_60m_throttles_disk_write_and_push(env):
    e = env
    e.pod.pop('work_queue')
    (e.root / 'pods.json').write_text(json.dumps([e.pod]))
    path = e.root / 'alerts/pod-idle-empty'
    for _ in range(4):
        assert observe(e, dry_run=True) == 0
        assert not path.exists()
        advance(e, 15)
    observe(e, dry_run=True)
    assert path.read_text() == 'empty\n'
    first_mtime = path.stat().st_mtime_ns
    assert e.push.call_count == 1
    # A restarted invocation reads the durable receipt. Another 5h45 is quiet.
    for _ in range(23):
        advance(e, 15)
        observe(e, dry_run=True)
        assert path.stat().st_mtime_ns == first_mtime
        assert e.push.call_count == 1
    advance(e, 15)  # exactly six hours since the first alert
    observe(e, dry_run=True)
    assert path.stat().st_mtime_ns != first_mtime
    assert e.push.call_count == 2
    for call in e.push.call_args_list:
        assert call.args[0][1:] == ['empty', 'Pod idle']
    e.final.assert_not_called()
    e.dispatch.assert_not_called()
    assert len(pods.registry(e.root)) == 1


@pytest.mark.parametrize('reset', ['cpu', 'gpu', 'unreachable', 'resources', 'gap', 'registry'])
def test_unknown_owner_idle_alert_evidence_resets(env, reset):
    e = env
    e.pod.pop('work_queue')
    for _ in range(3):
        observe(e)
        advance(e, 15)
    if reset == 'cpu':
        e.metric['cpu_frac'] = .1
    elif reset == 'gpu':
        e.pod['gpus'] = e.metric['gpus'] = 2
        e.metric['gpu_util'] = 1
    elif reset == 'unreachable':
        e.probe.side_effect = OSError('secret')
    elif reset == 'resources':
        e.metric['vcpus'] = 16
    elif reset == 'gap':
        advance(e, 2)
    else:
        e.pod['owner_note'] += ' changed'
    observe(e)
    e.metric.update(cpu_frac=.01, gpu_util=0, vcpus=32)
    e.probe.side_effect = lambda _: dict(e.metric)
    advance(e, 15)
    observe(e)
    assert not (e.root / 'alerts/pod-idle-empty').exists()
    e.final.assert_not_called()
    e.dispatch.assert_not_called()


def test_idle_alert_failed_push_retries_without_rewriting_alert(env):
    e = env
    e.push.return_value = SimpleNamespace(returncode=1)
    pods.reap_alert(e.root, e.pod, 'idle', now=e.clock.now())
    path = e.root / 'alerts/pod-idle-empty'
    mtime = path.stat().st_mtime_ns
    advance(e, 15)
    e.push.return_value = SimpleNamespace(returncode=0)
    pods.reap_alert(e.root, e.pod, 'idle', now=e.clock.now())
    pods.reap_alert(e.root, e.pod, 'idle', now=e.clock.now())
    assert e.push.call_count == 2
    assert path.stat().st_mtime_ns == mtime
    # Reused names with a new pod identity do not inherit the old throttle.
    pods.reap_alert(e.root, dict(e.pod, pod_id='replacement'), 'idle', now=e.clock.now())
    assert e.push.call_count == 3


@pytest.mark.parametrize('value', ['done-file:/workspace/HARNESS_DONE', 'harness',
                                  'done-file:/tmp/a file;$(false)'])
def test_set_ownership_preserves_registry_and_appends_journal(env, value):
    e = env
    other = dict(e.pod, name='other', pod_id='def456', ssh_target=('def456-1' + chr(64) + 'ssh.runpod.io'))
    (e.root / 'pods.json').write_text(json.dumps([e.pod, other]))
    e.pod.pop('work_queue')
    # Put undeclared ownership in the first row so all parameter cases change it.
    (e.root / 'pods.json').write_text(json.dumps([e.pod, other]))
    before = (e.root / 'pods.json').read_bytes()
    assert rh.main(['--root', str(e.root), 'pods', 'set', 'empty', '--work-queue', value]) == 0
    assert pods.registry(e.root) == [dict(e.pod, work_queue=value), other]
    assert (e.root / 'pods.json.bak-ownership').read_bytes() == before
    journal = harvest.rows(e.root / 'POD_REGISTRY.jsonl')
    assert [r['event'] for r in journal] == ['SET_INTENT', 'SET_COMMITTED']
    assert journal[-1]['after'] == value and journal[-1]['before'] is None
    # A second edit appends history and preserves the first backup.
    new_value = 'harness' if value != 'harness' else 'done-file:/workspace/DONE'
    assert rh.main(['--root', str(e.root), 'pods', 'set', 'empty', '--work-queue', new_value]) == 0
    assert harvest.rows(e.root / 'POD_REGISTRY.jsonl')[:2] == journal
    assert len(harvest.rows(e.root / 'POD_REGISTRY.jsonl')) == 4
    assert (e.root / 'pods.json.bak-ownership').read_bytes() == before
    e.probe.assert_not_called()
    e.dispatch.assert_not_called()


@pytest.mark.parametrize('value', ['legacy', 'done-file:', 'done-file:relative', 'done-file:/',
    'done-file:/workspace/../DONE', 'done-file:/workspace/./DONE', 'done-file://workspace/DONE',
    'done-file:/workspace/DONE/', 'done-file:/workspace/DONE\n'])
def test_invalid_ownership_declaration_rejected(value):
    with pytest.raises(ValueError):
        pods.work_queue(value)


def test_add_accepts_done_file(env):
    e = env
    (e.root / 'pods.json').write_text('[]')
    (e.root / 'lanes/team').mkdir(parents=True)
    assert rh.main(['--root', str(e.root), 'pods', 'add', 'empty', '--pod-id', 'abc123',
        '--ssh-target', e.pod['ssh_target'], '--lane', 'team', '--dollars-per-hour', '.96',
        '--owner-note', 'fixture', '--results', '/workspace/result', '--harvest-dest', str(e.root / 'saved'),
        '--work-queue', 'done-file:/workspace/HARNESS_DONE']) == 0
    assert pods.registry(e.root)[0]['work_queue'] == 'done-file:/workspace/HARNESS_DONE'


@pytest.mark.parametrize('state', ['older-results', 'newer-results', 'equal-mtime', 'missing', 'empty-roots'])
def test_actual_done_file_reader_mtime_rule(tmp_path, state):
    import os
    root = tmp_path.resolve()
    results = root / 'results'
    results.mkdir()
    result = results / 'nested' / 'answer'
    result.parent.mkdir()
    result.write_text('science')
    marker = root / 'HARNESS_DONE'
    now_ns = time.time_ns()
    if state != 'missing':
        marker.write_text('done')
        os.utime(marker, ns=(now_ns, now_ns))
    result_ns = now_ns - 1_000_000_000 if state == 'older-results' else now_ns + 1_000_000_000
    if state == 'equal-mtime':
        result_ns = now_ns
    os.utime(result, ns=(result_ns, result_ns))
    if state == 'empty-roots':
        with pytest.raises(ValueError):
            pods.done_file_snapshot(str(marker), [])
        return
    evidence = pods.done_file_snapshot(str(marker), [str(results)], now_ns=now_ns)
    assert evidence['exists'] is (state != 'missing')
    assert evidence['empty'] is (state == 'older-results')


def test_done_file_reader_scans_all_roots_and_replacement_changes_generation(tmp_path):
    import os
    root = tmp_path.resolve()
    a, b, marker = root / 'a', root / 'b', root / 'DONE'
    a.mkdir()
    b.mkdir()
    (a / 'old').write_text('a')
    (b / 'old').write_text('b')
    marker.write_text('done')
    now_ns = time.time_ns()
    os.utime(marker, ns=(now_ns, now_ns))
    first = pods.done_file_snapshot(str(marker), [str(a), str(b)])
    assert first['empty']
    (b / 'new').write_text('later')
    later_ns = now_ns + 1_000_000_000
    os.utime(b / 'new', ns=(later_ns, later_ns))
    second = pods.done_file_snapshot(str(marker), [str(a), str(b)])
    assert not second['empty'] and second['generation'] != first['generation']
    marker.rename(root / 'removed-marker')
    assert not pods.done_file_snapshot(str(marker), [str(a), str(b)])['exists']
    marker.write_text('done')
    third = pods.done_file_snapshot(str(marker), [str(a), str(b)])
    assert third['generation'] != second['generation']


@pytest.mark.parametrize('unsafe', ['result-link', 'root-link', 'marker-link', 'missing-root'])
def test_done_file_reader_fails_closed_on_unsafe_paths(tmp_path, unsafe):
    root = tmp_path.resolve()
    result = root / 'result'
    result.mkdir()
    marker = root / 'DONE'
    marker.write_text('done')
    if unsafe == 'result-link':
        (result / 'link').symlink_to(marker)
    elif unsafe == 'root-link':
        link = root / 'linked-root'
        link.symlink_to(result, target_is_directory=True)
        result = link
    elif unsafe == 'marker-link':
        link = root / 'linked-marker'
        link.symlink_to(marker)
        marker = link
    else:
        result = root / 'missing'
    with pytest.raises((ValueError, OSError)):
        pods.done_file_snapshot(str(marker), [str(result)])


@pytest.mark.parametrize('changed', ['missing', 'replaced', 'results', 'busy', 'keep'])
def test_done_file_copy_ack_rechecks_authorization(done_env, monkeypatch, changed):
    e = done_env
    manifest = dict(files={}, directories=[], bytes=0)
    def copy(*args):
        if changed == 'missing':
            e.metric['done_file'].update(exists=False, empty=False, generation='missing')
        elif changed in ('replaced', 'results'):
            e.metric['done_file']['generation'] = changed
        elif changed == 'busy':
            e.metric['cpu_frac'] = .2
        else:
            (e.root / 'pods.json').write_text(json.dumps([dict(e.pod, keep=True)]))
        return manifest
    monkeypatch.setattr(pods, 'copy_results', copy)
    hello = dict(op='hello', lane=e.pod['lane'], owner=e.pod['pod_id'], vcpus=32, gpus=0,
                 capabilities=['reap-v1'], running=0, pending=0)
    channel = SimpleNamespace(receive=Mock(side_effect=[hello, dict(op='copy', manifest=manifest),
        dict(op='ended', verified_shutdown=True)]), send=Mock())
    with pytest.raises(ValueError):
        pods.serve_controller(channel, pods.WorkQueue(e.root, 'team', e.pod['pod_id'], 32, 0),
            e.root / 'saved', e.pod, notify=Mock(return_value=True), mode='reap', require_reaper=True)
    assert any(c.args[0].get('op') == 'copied' and c.args[0].get('error')
               for c in channel.send.call_args_list)
    assert not any(c.args[0].get('op') == 'copied' and 'manifest' in c.args[0]
                   for c in channel.send.call_args_list)


def test_done_file_change_before_copy_handshake_rejected(done_env):
    e = done_env
    hello = dict(op='hello', lane=e.pod['lane'], owner=e.pod['pod_id'], vcpus=32, gpus=0,
                 capabilities=['reap-v1'], running=0, pending=0)
    channel = SimpleNamespace(receive=Mock(return_value=hello), send=Mock())
    with pytest.raises(ValueError, match='before copy'):
        pods.serve_controller(channel, pods.WorkQueue(e.root, 'team', e.pod['pod_id'], 32, 0),
            e.root / 'saved', e.pod, mode='reap', require_reaper=True,
            expected_generation='previous-marker-and-results')
    channel.send.assert_called_once_with(dict(ok=False))


def test_done_file_remote_command_executes_read_only_with_quoted_paths(tmp_path):
    import os
    root = tmp_path.resolve()
    result = root / "results with 'quotes'; $(false)"
    result.mkdir()
    (result / 'answer').write_text('science')
    marker = root / "DONE with 'quotes'; $(false)"
    marker.write_text('done')
    now_ns = time.time_ns()
    os.utime(marker, ns=(now_ns, now_ns))
    pod = dict(work_queue='done-file:' + str(marker), results=[str(result)])
    command = pods.done_file_command(pod)
    before = sorted(str(p.relative_to(root)) for p in root.rglob('*'))
    actual = subprocess.run(['/bin/sh', '-c', command], check=True, text=True, capture_output=True)
    evidence = json.loads(actual.stdout)
    assert evidence['exists'] and evidence['empty']
    assert evidence == pods.done_file_snapshot(str(marker), [str(result)])
    assert sorted(str(p.relative_to(root)) for p in root.rglob('*')) == before
    assert len(command) < 4096


def test_done_file_only_compares_mtimes_of_files_changed_in_last_20m(tmp_path):
    import os
    root = tmp_path.resolve()
    results, marker = root / 'results', root / 'DONE'
    results.mkdir()
    (results / 'answer').write_text('old result')
    marker.write_text('old marker')
    now_ns = time.time_ns()
    os.utime(marker, ns=(now_ns - 3600 * 10**9, now_ns - 3600 * 10**9))
    # Simulate 21 minutes passing after all ctime/mtime changes. The old result
    # can be newer than the marker yet is outside the requested 20-minute set.
    evidence = pods.done_file_snapshot(str(marker), [str(results)], now_ns=now_ns + 21 * 60 * 10**9)
    assert evidence['empty']
    # At the current time, that same newer result blocks eligibility.
    assert not pods.done_file_snapshot(str(marker), [str(results)], now_ns=now_ns)['empty']


def test_done_file_generation_change_between_idle_samples_restarts_grace(done_env):
    e = done_env
    idle_window(e)
    e.metric['done_file']['generation'] = 'a-result-changed-between-samples'
    observe(e)
    advance(e, 15)
    observe(e)
    e.final.assert_not_called()
    advance(e, 15)
    observe(e)
    assert len(shutdown_calls(e)) == 1


def test_unknown_gpu_idle_alert_uses_exact_zero_even_with_busy_cpu(env):
    e = env
    e.pod.pop('work_queue')
    e.pod['gpus'] = e.metric['gpus'] = 2
    e.metric.update(gpu_util=0, cpu_frac=.8)
    for _ in range(5):
        observe(e)
        advance(e, 15)
    assert (e.root / 'alerts/pod-idle-empty').read_text() == 'empty\n'
    e.final.assert_not_called()
    e.dispatch.assert_not_called()
