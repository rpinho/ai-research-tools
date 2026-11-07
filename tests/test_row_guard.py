"""A row whose input is broken stops after two failures instead of restarting forever.

11-06: when a HOLD lifted, five friends tw rows restarted on a pod that was long gone, each
dying on the same KeyError before any science, every four minutes, with a model call each
time, until someone killed them by hand.
"""
import importlib.util
import json
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('rg', ROOT / 'harness/tools/row_guard.py')
rg = importlib.util.module_from_spec(spec); spec.loader.exec_module(rg)


def setup(tmp_path, monkeypatch):
    queue = tmp_path / 'alerts.jsonl'
    monkeypatch.setenv('RH_AGENT_ALERTS', str(queue))
    work = tmp_path / 'work'
    work.mkdir()
    return tmp_path / 'state', work, queue


def alerts(queue):
    return [json.loads(l) for l in queue.read_text().splitlines() if l.strip()] \
        if queue.exists() else []


def test_two_pre_science_failures_stop_the_row_and_alert_once(tmp_path, monkeypatch):
    state, work, queue = setup(tmp_path, monkeypatch)
    now = time.time()
    code, msg = rg.guard('friends/tw/T1', rc=1, since=now, watch=[work], state=state,
                         label='tw T1', lane='friends')
    assert code == 0 and 'failure 1 of 2' in msg, 'one bad run is not a verdict'
    assert alerts(queue) == []
    code, msg = rg.guard('friends/tw/T1', rc=1, since=now, watch=[work], state=state,
                         label='tw T1', lane='friends')
    assert code == 3 and 'STALE_INPUT' in msg and 'lane=friends' in msg
    assert len(alerts(queue)) == 1 and 'STALE_INPUT' in alerts(queue)[0]['message']
    # Still stopped, and the lane is not alerted again for the same row.
    for _ in range(3):
        assert rg.guard('friends/tw/T1', rc=1, since=now, watch=[work], state=state)[0] == 3
    assert len(alerts(queue)) == 1


def test_a_run_that_produced_work_is_an_ordinary_failure(tmp_path, monkeypatch):
    """A job that got somewhere and then failed must keep its retries: that is not stale input."""
    state, work, queue = setup(tmp_path, monkeypatch)
    now = time.time()
    for _ in range(5):
        (work / 'fit.json').write_text('{"bound": 1.07}')
        assert rg.guard('k', rc=1, since=now, watch=[work], state=state)[0] == 0
    assert alerts(queue) == []


def test_progress_clears_an_earlier_failure(tmp_path, monkeypatch):
    state, work, queue = setup(tmp_path, monkeypatch)
    now = time.time()
    assert rg.guard('k', rc=1, since=now, watch=[work], state=state)[0] == 0
    (work / 'certificate.txt').write_text('a certificate')
    assert rg.guard('k', rc=1, since=now, watch=[work], state=state)[0] == 0
    # The counter restarted, so the next bare failure is the first again.
    (work / 'certificate.txt').unlink()
    later = time.time() + 1
    assert 'failure 1 of 2' in rg.guard('k', rc=1, since=later, watch=[work], state=state)[1]


def test_success_clears_the_marker(tmp_path, monkeypatch):
    state, work, queue = setup(tmp_path, monkeypatch)
    now = time.time()
    rg.guard('k', rc=1, since=now, watch=[work], state=state)
    assert rg.guard('k', rc=1, since=now, watch=[work], state=state)[0] == 3
    (work / 'fit.json').write_text('{"bound": 1.07}')
    assert rg.guard('k', rc=0, since=now, watch=[work], state=state)[0] == 0
    assert not list(state.glob('*.STALE_INPUT'))


def test_exit_zero_with_nothing_written_does_not_clear_anything(tmp_path, monkeypatch):
    """Hole 1 of the DB15 restart loop: the worker caught its own blocker, wrote "blocked" and
    exited 0, so 516 runs that did nothing each counted as a success."""
    state, work, queue = setup(tmp_path, monkeypatch)
    now = time.time()
    assert rg.guard('k', rc=0, since=now, watch=[work], state=state)[0] == 0
    code, msg = rg.guard('k', rc=0, since=now, watch=[work], state=state)
    assert code == 3 and 'STALE_INPUT' in msg
    assert 'exited 0 each time' in msg, msg


def test_the_db15_restart_shape_stops_on_the_second_run(tmp_path, monkeypatch):
    """Hole 2: the worker rewrote STATUS.md and PLAN.md and wrote a uniquely named receipt per
    restart. Every run looked like progress, for 516 runs over 18 hours on a missing pod."""
    state, work, queue = setup(tmp_path, monkeypatch)
    codes = []
    for n in range(1, 5):
        now = time.time()
        (work / 'STATUS.md').write_text('blocked: KeyError(mscr) before SSH')
        (work / 'PLAN.md').write_text('next bar: ...')
        (work / f'CHECKPOINT_RESTART{n}_SHA256.json').write_text(
            '{"restart": %d, "science_executed": false}' % n)
        (work / f'process_audit_restart{n}.json').write_text('{}')
        (work / f'preflight_2026110{n}T131700Z.txt').write_text('exit 1')
        codes.append(rg.guard('tw/diogo/DB15', rc=0, since=now, watch=[work], state=state,
                              label='tw diogo DB15', lane='friends')[0])
    assert codes == [0, 3, 3, 3], codes
    assert len(alerts(queue)) == 1
    # And the row resumes the moment the pod is back and it writes something real.
    (work / 'sos_q9_exact.json.gz').write_bytes(b'\x1f\x8b')
    assert rg.guard('tw/diogo/DB15', rc=0, since=time.time() - 1, watch=[work], state=state)[0] == 0


def test_a_lane_can_name_its_own_receipts(tmp_path, monkeypatch):
    state, work, queue = setup(tmp_path, monkeypatch)
    now = time.time()
    (work / 'pod_preflight.sh').write_text('ssh ...')
    # Not bookkeeping by default, so it counts as work however many times it is rewritten.
    for _ in range(3):
        assert rg.guard('k', rc=0, since=now, watch=[work], state=state)[0] == 0
    # The lane says it is a receipt, and the same runs now stop the row.
    codes = [rg.guard('k2', rc=0, since=now, watch=[work], state=state,
                      ignore=['pod_preflight.sh'])[0] for _ in range(2)]
    assert codes == [0, 3], codes


def test_a_log_or_prompt_is_not_progress(tmp_path, monkeypatch):
    """A traceback is not science; otherwise every crash would look like work."""
    state, work, queue = setup(tmp_path, monkeypatch)
    now = time.time()
    (work / 'job.log').write_text('Traceback...\nKeyError: mscr\n')
    (work / 'job.log.prompt').write_text('the spec')
    assert rg.guard('k', rc=1, since=now, watch=[work], state=state)[0] == 0
    assert rg.guard('k', rc=1, since=now, watch=[work], state=state)[0] == 3


def test_a_missing_watch_directory_does_not_crash(tmp_path, monkeypatch):
    state, _, _ = setup(tmp_path, monkeypatch)
    code, _ = rg.guard('k', rc=1, since=time.time(), watch=[tmp_path / 'nope'], state=state)
    assert code == 0


def test_the_key_cannot_escape_the_state_directory(tmp_path, monkeypatch):
    state, work, _ = setup(tmp_path, monkeypatch)
    rg.guard('../../etc/passwd', rc=1, since=time.time(), watch=[work], state=state)
    assert all(p.parent == state for p in state.glob('*'))
