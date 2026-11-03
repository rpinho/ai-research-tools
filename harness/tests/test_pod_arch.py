"""Only local simulations: no SSH, provider requests, real pushes or pod actions."""
import concurrent.futures
import importlib.util
import json
import multiprocessing
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pods
import rh

spec = importlib.util.spec_from_file_location('pod_create_gate', Path(__file__).resolve().parents[2] / 'tools/pod-create-gate.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)



@pytest.fixture(autouse=True)
def enforce_capacity_tests(tmp_path, monkeypatch):
    # Original capacity regression tests explicitly exercise enforcement, not rollout defaults.
    config = tmp_path / 'gate-config.json'
    config.write_text(json.dumps({'mode': 'enforce', 'enforce_lanes': sorted(pods.LANES)}))
    monkeypatch.setattr(gate, 'CONFIG', config)
    monkeypatch.setattr(gate, 'AUDIT', tmp_path / 'gate-audit.jsonl')


def target(i, **kw):
    return dict(id='job-' + str(i), lane='team', argv=[sys.executable, '-c', 'pass'],
                runnable=True, **kw)


def seed(root, rows):
    (root / pods.QUEUE).write_text(json.dumps(rows))


def event(vcpus=32, gpu=False, lane='team', gpus=1):
    return dict(tool_name='mcp__runpod__create-pod', tool_input=dict(body=dict(
        env={'RH_LANE': lane}, vcpuCount=vcpus, computeType='GPU' if gpu else 'CPU',
        gpuCount=gpus if gpu else 0)))


@pytest.mark.parametrize('cpu,cores,gpus,gpu,expected', [
    (32, 4, 0, False, 8), (13.6, 4, 0, False, 3), (32, 8, 0, False, 4),
    (16, 4, 1, True, 1), (32, 4, 4, True, 4), (32, 4, 0, True, 0)])
def test_slots(cpu, cores, gpus, gpu, expected):
    assert pods.slot_count(cpu, cores, gpus, gpu) == expected


def claim_process(root, owner):
    q = pods.WorkQueue(Path(root), 'team', owner, 32, 0)
    ids = []
    while True:
        row = q.claim(32, 0)
        if row is None:
            return ids
        ids.append(row['id'])


def test_atomic_claims_two_processes(tmp_path):
    seed(tmp_path, [target(i) for i in range(24)])
    with concurrent.futures.ProcessPoolExecutor(2, mp_context=multiprocessing.get_context('fork')) as pool:
        results = list(pool.map(claim_process, [str(tmp_path)] * 2, ['pod-a', 'pod-b']))
    ids = results[0] + results[1]
    assert len(ids) == len(set(ids)) == 24
    claims = json.loads((tmp_path / pods.CLAIMS).read_text())
    assert all(c['owner'] in ('pod-a', 'pod-b') and c['token'] for c in claims.values())


def test_dependencies_owner_failed_and_cpu_gpu_filter(tmp_path):
    rows = [
        target(0, owner='legacy'), target(1), target(2, depends_on=['job-1']),
        target(3, gpu_bound=True)]
    seed(tmp_path, rows)
    q = pods.WorkQueue(tmp_path, 'team', 'a', 32, 0)
    first = q.claim(32, 0)
    assert first['id'] == 'job-1'
    assert q.claim(32, 0) is None
    with pytest.raises(ValueError):
        pods.WorkQueue(tmp_path, 'team', 'other', 32, 0).finish(first, 0)
    q.finish(first, 0)
    assert q.claim(32, 0)['id'] == 'job-2'
    assert pods.WorkQueue(tmp_path, 'team', 'gpu', 16, 1).claim(16, 1)['id'] == 'job-3'


class Process:
    def __init__(self):
        self.code = None

    def poll(self):
        return self.code


class LocalQueue:
    def __init__(self, queue):
        self.queue = queue
        self.copy = Mock(return_value={'bytes': 0})
        self.alert = Mock()

    def claim(self, *args):
        return self.queue.claim(*args)

    def finish(self, *args):
        return self.queue.finish(*args)


def test_capacity_and_refill(tmp_path):
    seed(tmp_path, [target(i) for i in range(10)])
    processes = []
    def launch(t, gpu):
        p = Process()
        processes.append(p)
        return p
    runner = pods.SlotRunner(tmp_path / 'workspace', 32, launch=launch)
    q = pods.WorkQueue(tmp_path, 'team', 'pod', 32, 0)
    runner.tick(q)
    assert len(runner.active) == 8
    processes[2].code = 0
    runner.tick(q)
    assert len(runner.active) == 8
    assert len(processes) == 9
    assert json.loads((tmp_path / pods.CLAIMS).read_text())['job-2']['status'] == 'done'


def test_mixed_declared_cores_fill_capacity(tmp_path):
    seed(tmp_path, [target(0, cores=12), target(1, cores=8), target(2), target(3, cores=8), target(4)])
    runner = pods.SlotRunner(tmp_path / 'w', 32, launch=lambda t, g: Process())
    runner.tick(pods.WorkQueue(tmp_path, 'team', 'pod', 32, 0))
    assert sum(pods.target_cores(t) for t, _, _ in runner.active.values()) == 32
    assert len(runner.active) == 4


def test_gpu_assigns_distinct_devices_and_never_cpu(tmp_path):
    seed(tmp_path, [target(0), *[target(i, gpu_bound=True) for i in range(1, 4)]])
    devices = []
    runner = pods.SlotRunner(tmp_path / 'w', 16, 2,
                            launch=lambda t, g: (devices.append(g), Process())[1])
    runner.tick(pods.WorkQueue(tmp_path, 'team', 'gpu', 16, 2))
    assert devices == [0, 1]
    assert 'job-0' not in json.loads((tmp_path / pods.CLAIMS).read_text())


def test_idle_copy_failure_never_terminates_then_retries(tmp_path):
    clock = [0]
    terminated = Mock()
    runner = pods.SlotRunner(tmp_path / 'w', 32, terminate=terminated, clock=lambda: clock[0])
    queue = LocalQueue(pods.WorkQueue(tmp_path, 'team', 'p', 32, 0))
    runner.tick(queue)
    clock[0] = 1199
    runner.tick(queue)
    queue.copy.assert_not_called()
    clock[0] = 1200
    queue.copy.side_effect = OSError('disk full')
    runner.tick(queue)
    terminated.assert_not_called()
    queue.alert.assert_called_once()
    clock[0] = 1260
    queue.copy.side_effect = None
    order = []
    queue.copy.side_effect = lambda: order.append('copy')
    terminated.side_effect = lambda: order.append('terminate')
    runner.tick(queue)
    assert order == ['copy', 'terminate']
    assert runner.ended


def test_launch_failure_retains_failed_claim(tmp_path):
    seed(tmp_path, [target(0)])
    q = pods.WorkQueue(tmp_path, 'team', 'pod', 4, 0)
    runner = pods.SlotRunner(tmp_path / 'w', 4, launch=Mock(side_effect=OSError('missing')))
    runner.tick(q)
    runner.tick(q)
    assert json.loads((tmp_path / pods.CLAIMS).read_text())['job-0']['status'] == 'failed'
    assert q.claim(4, 0) is None


def test_plan_and_hook_cpu_threshold_and_receipt(tmp_path):
    seed(tmp_path, [target(i) for i in range(4)])
    assert not pods.plan(tmp_path, 'team', 32, now=100)['pass']
    assert gate.gate(event(), tmp_path, now=100)[0] == 2
    seed(tmp_path, [target(i) for i in range(5)])
    result = pods.plan(tmp_path, 'team', 32, now=100)
    assert result['pass'] and result['fraction'] == .625
    assert 'floor(32/4)=8' in result['message']
    assert gate.gate(event(), tmp_path, now=101)[0] == 0
    assert gate.gate(event(), tmp_path, now=1901)[0] == 2
    assert gate.gate(event(), tmp_path, now=99)[0] == 2
    assert gate.gate(event(64), tmp_path, now=101)[0] == 2
    assert gate.gate(event(lane='theory'), tmp_path, now=101)[0] == 2


def test_gpu_plan_cpu_only_fail_and_matching_shape(tmp_path):
    seed(tmp_path, [target(i) for i in range(20)])
    assert not pods.plan(tmp_path, 'team', 16, 1, now=100)['pass']
    assert gate.gate(event(16, True), tmp_path, now=100)[0] == 2
    seed(tmp_path, [target(0, gpu_bound=True)])
    assert pods.plan(tmp_path, 'team', 16, 1, now=100)['pass']
    assert gate.gate(event(16, True), tmp_path, now=101)[0] == 0
    assert gate.gate(event(16, True, gpus=2), tmp_path, now=101)[0] == 2
    assert not pods.plan(tmp_path, 'team', 16, 2, now=101)['pass']


def test_receipt_invalidated_by_claim_and_fail(tmp_path):
    seed(tmp_path, [target(i) for i in range(5)])
    assert pods.plan(tmp_path, 'team', 32, now=100)['pass']
    pods.WorkQueue(tmp_path, 'team', 'p', 32, 0).claim(32, 0)
    assert gate.gate(event(), tmp_path, now=101)[0] == 2
    assert not pods.plan(tmp_path, 'team', 32, now=101)['pass']
    assert json.loads((tmp_path / pods.RECEIPTS).read_text()) == []


def test_plan_dependencies_and_owned_are_not_fuel(tmp_path):
    seed(tmp_path, [target(0, owner='driver'), target(1, depends_on=['missing']), target(2)])
    assert not pods.plan(tmp_path, 'team', 32)['pass']
    seed(tmp_path, [target(0, cores=1)])
    assert not pods.plan(tmp_path, 'team', 32)['pass']


def test_plan_cli_local_and_invalid_gpu_count(tmp_path, capsys):
    seed(tmp_path, [target(i) for i in range(5)])
    assert rh.main(['--root', str(tmp_path), 'pods', 'plan', 'team', '--vcpus', '32']) == 0
    assert 'PASS' in capsys.readouterr().out
    assert rh.main(['--root', str(tmp_path), 'pods', 'plan', 'team', '--vcpus', '16', '--gpu']) == 1
    assert rh.main(['--root', str(tmp_path), 'pods', 'plan', 'team', '--vcpus', '16', '--gpu', '--gpus', '0']) == 2


def test_hook_malformed_and_noncreate(tmp_path):
    assert gate.gate(event(), tmp_path)[0] == 2
    assert gate.gate({'tool_name': 'mcp__runpod__list-pods'}, tmp_path)[0] == 0
    for field, bad in [('vcpuCount', None), ('vcpuCount', True),
                       ('computeType', None), ('gpuCount', 1)]:
        e = event()
        e['tool_input']['body'][field] = bad
        assert gate.gate(e, tmp_path)[0] == 2
    # No lane can be scoped for enforcement when env/name are both absent.
    e = event()
    e['tool_input']['body']['env'] = {}
    assert gate.gate(e, tmp_path)[0] == 0
    result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[2] / 'tools/pod-create-gate.py')],
                            input='{', text=True, capture_output=True,
                                env=dict(__import__('os').environ, HOME=str(tmp_path),
                                         RH_POD_GATE_AUDIT=str(tmp_path/'audit.jsonl')))
    assert result.returncode == 0
    assert 'additionalContext' in result.stdout
    assert json.loads((tmp_path/'audit.jsonl').read_text())['receipt_status'] == 'unparsed'


def test_copy_handshake_byte_count_hashes_and_fake_terminate(tmp_path):
    seed(tmp_path, [])
    work = tmp_path / 'workspace'
    (work / 'nested').mkdir(parents=True)
    (work / 'nested/result.bin').write_bytes(bytes(range(256)) * 800)
    (work / 'empty').mkdir()
    (work / 'zero').touch()
    clock = [0]
    terminated = []
    runner = pods.SlotRunner(work, 32, clock=lambda: clock[0], terminate=lambda: terminated.append(True))
    a, b = socket.socketpair()
    a.settimeout(5)
    b.settimeout(5)
    errors = []
    with a, b, a.makefile('rwb', buffering=65536) as pod_stream, b.makefile('rwb', buffering=65536) as mini_stream:
        pc = pods.JsonChannel(pod_stream, pod_stream)
        mc = pods.JsonChannel(mini_stream, mini_stream)
        def controller():
            try:
                pods.serve_controller(mc, pods.WorkQueue(tmp_path, 'team', 'fake', 32, 0),
                                      tmp_path / 'mini', {'pod_id': 'fake', 'name': 'fake'},
                                      notify=Mock())
            except Exception as e:
                errors.append(e)
        thread = threading.Thread(target=controller)
        thread.start()
        q = pods.PodQueue(pc, work)
        runner.tick(q)
        clock[0] = 1200
        runner.tick(q)
        pc.send({'op': 'ended'})
        thread.join(5)
        assert not thread.is_alive() and not errors
    assert terminated == [True]
    snapshot = next((tmp_path / 'mini').iterdir())
    assert pods.workspace_manifest(snapshot) == pods.workspace_manifest(work)


def test_copy_rejects_corrupt_data_and_workspace_links(tmp_path):
    work = tmp_path / 'w'
    work.mkdir()
    (work / 'result').write_bytes(b'abc')
    manifest = pods.workspace_manifest(work)
    channel = Mock()
    channel.receive.return_value = {'op': 'data', 'data': 'YWJk'}  # same bytes, wrong hash
    with pytest.raises(ValueError, match='verification'):
        pods.copy_results(channel, tmp_path / 'copy', manifest)
    (work / 'link').symlink_to(work / 'result')
    with pytest.raises(ValueError, match='symlink'):
        pods.workspace_manifest(work)


def test_copy_failure_push_from_controller(tmp_path):
    channel = Mock()
    channel.receive.side_effect = [dict(op='copy', manifest={'files': {}, 'directories': [], 'bytes': 5}),
                                   dict(op='ended')]
    notify = Mock()
    pods.serve_controller(channel, None, tmp_path, {'pod_id': 'fake', 'name': 'fake'}, notify)
    notify.assert_called_once()
    assert channel.send.call_args[0][0].get('error') == 'copy failed'


def test_real_local_process_refills_and_copies(tmp_path):
    # Actual subprocesses, temporary workspaces and a fake termination callback only.
    rows = []
    for i in range(3):
        t = target(i)
        t['argv'] = [sys.executable, '-c', 'from pathlib import Path; Path("out-' + str(i) + '").write_text("done")']
        rows.append(t)
    seed(tmp_path, rows)
    copied = tmp_path / 'saved'
    clock = [0]
    ended = []
    q = LocalQueue(pods.WorkQueue(tmp_path, 'team', 'local', 8, 0))
    def copy():
        import shutil
        shutil.copytree(tmp_path / 'w', copied)
        assert pods.workspace_manifest(copied) == pods.workspace_manifest(tmp_path / 'w')
    q.copy.side_effect = copy
    runner = pods.SlotRunner(tmp_path / 'w', 8, clock=lambda: clock[0], terminate=lambda: ended.append(True))
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        runner.tick(q)
        claims = json.loads((tmp_path / pods.CLAIMS).read_text())
        if len(claims) == 3 and all(c['status'] == 'done' for c in claims.values()):
            break
        time.sleep(.01)
    assert all(c['status'] == 'done' for c in claims.values()) and len(claims) == 3
    clock[0] = 1200
    runner.tick(q)
    assert ended == [True]
    assert len(list(copied.glob('out-*'))) == 3


def test_pack_avoids_resource_fragmentation(tmp_path):
    seed(tmp_path, [target(0, cores=18), target(1, cores=17), target(2, cores=15)])
    assert pods.plan(tmp_path, 'team', 32)['fraction'] == 1
    runner = pods.SlotRunner(tmp_path / 'w', 32, launch=lambda t, g: Process())
    runner.tick(pods.WorkQueue(tmp_path, 'team', 'p', 32, 0))
    assert sum(pods.target_cores(t) for t, _, _ in runner.active.values()) == 32


def test_resources_measured_on_pod_v1_v2_and_gpus(tmp_path):
    (tmp_path / 'cpu.max').write_text('1360000 100000')
    assert pods.local_resources(tmp_path, nproc=128, gpu_probe=lambda: 'GPU 0: A40\n') == (13.6, 1)
    other = tmp_path / 'v1'
    (other / 'cpu').mkdir(parents=True)
    (other / 'cpu/cpu.cfs_quota_us').write_text('3200000')
    (other / 'cpu/cpu.cfs_period_us').write_text('100000')
    assert pods.local_resources(other, nproc=128, gpu_probe=lambda: '') == (32, 0)
    assert pods.local_resources(tmp_path / 'missing', nproc=8, gpu_probe=lambda: '') == (8, 0)


def test_copy_source_change_blocks_termination(tmp_path):
    work = tmp_path / 'w'
    work.mkdir()
    (work / 'result').write_text('before')
    before = pods.workspace_manifest(work)
    channel = Mock()
    def response():
        (work / 'result').write_text('changed')
        return dict(op='copied', manifest=before)
    channel.receive.side_effect = response
    with pytest.raises(ValueError, match='changed'):
        pods.PodQueue(channel, work).copy()


def test_plan_uses_whole_declared_slots_for_fractional_quota(tmp_path):
    seed(tmp_path, [target(0), target(1)])
    result = pods.plan(tmp_path, 'team', 13.6)
    assert result['pass'] and result['slots'] == 3
    assert result['fraction'] == pytest.approx(2 / 3)
    seed(tmp_path, [target(0, cores=8), target(1, cores=8), target(2, cores=8)])
    result = pods.plan(tmp_path, 'team', 32)
    assert result['pass'] and result['fraction'] == .75


def test_local_start_command_and_attach(tmp_path):
    """Exercise real serve/attach entrypoints through Unix sockets, with no SSH."""
    seed(tmp_path, [target(0)])
    socket_dir = tempfile.TemporaryDirectory(prefix='rh-', dir='/tmp')
    socket_path = Path(socket_dir.name) / 'runner.sock'
    work = tmp_path / 'workspace'
    marker = tmp_path / 'fake-termination'
    fake = json.dumps([sys.executable, '-c', 'from pathlib import Path; Path(' + repr(str(marker)) + ').touch()'])
    daemon = subprocess.Popen([sys.executable, str(ROOT / 'pod/runner.py'), 'serve',
        '--socket', str(socket_path), '--workspace', str(work), '--lane', 'team',
        '--owner', 'local', '--vcpus', '4', '--gpus', '0', '--terminate-json', fake],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    attach = None
    thread = None
    failures = []
    try:
        deadline = time.monotonic() + 8
        while not socket_path.exists() and daemon.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert socket_path.exists(), daemon.stderr.read().decode() if daemon.poll() is not None else 'no socket'
        attach = subprocess.Popen([sys.executable, str(ROOT / 'pod/runner.py'), 'attach',
            '--socket', str(socket_path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        def controller():
            try:
                pods.serve_controller(pods.JsonChannel(attach.stdout, attach.stdin),
                    pods.WorkQueue(tmp_path, 'team', 'local', 4, 0), tmp_path / 'saved',
                    {'pod_id': 'local', 'name': 'local'}, notify=Mock())
            except (EOFError, OSError, ValueError):
                pass  # deliberate local disconnect at test completion
            except Exception as e:
                failures.append(e)
        thread = threading.Thread(target=controller)
        thread.start()
        claims = {}
        while time.monotonic() < deadline:
            claims = pods.load_json(tmp_path / pods.CLAIMS, {})
            if claims.get('job-0', {}).get('status') == 'done':
                break
            time.sleep(.01)
        assert claims['job-0']['status'] == 'done'
        assert not marker.exists()  # 20-minute grace never elapsed
        assert not failures
    finally:
        if attach is not None:
            attach.stdin.close()
            try:
                attach.wait(timeout=3)
            except subprocess.TimeoutExpired:
                attach.kill()
                attach.wait()
            if thread:
                thread.join(3)
            attach.stdout.close()
            attach.stderr.close()
        daemon.terminate()  # only this test's newly created local process
        daemon.wait(timeout=3)
        daemon.stdout.close()
        daemon.stderr.close()
        socket_dir.cleanup()
