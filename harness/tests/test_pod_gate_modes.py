"""Capacity rollout: only temporary queues/config/audit files; never provider calls."""
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pods

HOOK = Path(__file__).resolve().parents[2] / 'tools/pod-create-gate.py'
DEADLINE = Path(__file__).resolve().parents[2] / 'tools/pod-deadline-gate.py'
spec = importlib.util.spec_from_file_location('pod_gate_modes', HOOK)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, 'CONFIG', tmp_path/'config.json')
    monkeypatch.setattr(gate, 'AUDIT', tmp_path/'audit.jsonl')


def event(lane='team', **kwargs):
    body = dict(env={'RH_LANE': lane}, vcpuCount=32, computeType='CPU', gpuCount=0)
    body.update(kwargs)
    return {'tool_name': 'mcp__plugin_runpod_runpod__create-pod', 'tool_input': {'body': body}}


def row():
    return json.loads(gate.AUDIT.read_text().splitlines()[-1])


def configure(mode='enforce', lanes=('team',)):
    gate.CONFIG.write_text(json.dumps({'mode': mode, 'enforce_lanes': list(lanes)}))


@pytest.mark.parametrize('bad', [None, [], {}, {'tool_input': None}, {'tool_input': {'body': []}},
                               event(vcpuCount=None), event(vcpuCount=True), event(vcpuCount=math.nan),
                               event(gpuCount='1'), event(env={'RH_LANE': ['team']})])
def test_audit_never_denies_unparsed(bad, tmp_path):
    assert gate.gate(bad, tmp_path)[0] == 0
    assert row()['receipt_status'] == 'unparsed'
    assert row()['would_enforce'] == 'deny'
    assert row()['decision'] == 'allow'


@pytest.mark.parametrize('mode', [None, 'audit'])
def test_missing_config_defaults_audit_and_reminds_enqueue(mode, tmp_path):
    if mode:
        configure(mode)
    code, context = gate.gate(event(), tmp_path, now=100)
    assert code == 0 and 'rh pods plan team --vcpus 32' in context
    assert 'rh pods enqueue' in context
    assert row() == dict(utc='1970-01-01T00:01:40Z', lane='team',
                         shape={'vcpus': 32, 'gpus': 0}, receipt_status='missing_or_invalid',
                         mode='audit', would_enforce='deny', decision='allow')


def test_enforce_only_selected_lane(tmp_path):
    configure()
    assert gate.gate(event(), tmp_path)[0] == 2
    for lane in ('theory', 'orbits', 'toy-evolution'):
        assert gate.gate(event(lane), tmp_path)[0] == 0
    assert gate.gate(event(vcpuCount=None), tmp_path)[0] == 2
    assert gate.gate(event(env={}), tmp_path)[0] == 0  # No identifiable opted-in lane.
    configure(lanes=())
    assert gate.gate(event(), tmp_path)[0] == 0


@pytest.mark.parametrize('lane', ['restricted-a', 'restricted-b'])
@pytest.mark.parametrize('broken', [True, False])
def test_excluded_lanes_never_enforced(lane, broken, tmp_path, monkeypatch):
    configure(lanes=('team', 'restricted-a', 'restricted-b'))
    receipt = Mock(side_effect=AssertionError('must not check excluded lane'))
    monkeypatch.setattr(pods, 'receipt_matches', receipt)
    assert gate.gate(event(lane, vcpuCount=None if broken else 32), tmp_path)[0] == 0
    assert row()['would_enforce'] == 'allow'
    receipt.assert_not_called()


@pytest.mark.parametrize('value', ['{', '[]', '{"mode":"deny"}', '{"mode":"enforce","enforce_lanes":"team"}'])
def test_bad_config_is_visible_audit(value, tmp_path):
    gate.CONFIG.write_text(value)
    assert gate.gate(event(), tmp_path)[0] == 0
    assert row()['mode'] == 'audit' and row()['config_status']


def test_pass_and_receipt_failure(tmp_path, monkeypatch):
    configure()
    monkeypatch.setattr(pods, 'receipt_matches', lambda *args: True)
    assert gate.gate(event(), tmp_path)[0] == 0
    assert row()['receipt_status'] == 'PASS'
    assert row()['would_enforce'] == 'allow'
    monkeypatch.setattr(pods, 'receipt_matches', Mock(side_effect=RuntimeError('SENSITIVE')))
    configure('audit')
    code, context = gate.gate(event(), tmp_path)
    assert code == 0 and row()['receipt_status'] == 'unreadable'
    assert 'SENSITIVE' not in context + gate.AUDIT.read_text()


def test_audit_write_failure_still_allows_and_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, 'append_audit', Mock(side_effect=OSError('full disk')))
    code, context = gate.gate(event(), tmp_path)
    assert code == 0 and 'could not be written' in context


@pytest.mark.parametrize('env', [{'RH_LANE': 'theory'}, [{'key': 'RH_LANE', 'value': 'theory'}],
                                 [{'name': 'RH_LANE', 'value': 'theory'}]])
def test_env_forms(env):
    assert gate.shape(event('theory', env=env)) == ('theory', 32, 0)


@pytest.mark.parametrize('name,lane', [('toy-evolution-nightly', 'toy-evolution'),
    ('rh-team-cpu-1102', 'team'), ('ORBITS-gpu', 'orbits'), ('restricted-a-run', 'restricted-a'),
    ('restricted-b-run', 'restricted-b')])
def test_name_inference(name, lane):
    assert gate.shape(event(env={}, name=name)) == (lane, 32, 0)


def test_ambiguous_or_contradictory_name_stays_unparsed(tmp_path):
    for e in (event(env={}, name='theory-orbits'), event('theory', name='team-worker')):
        assert gate.gate(e, tmp_path)[0] == 0
        assert row()['receipt_status'] == 'unparsed'


def test_deadline_body_input_and_flat_resource_keys(tmp_path):
    e = event(env={}, name='toy-evolution-1102', entrypoint=['/bin/bash', '-c'],
              cmd=['(sleep 3600; echo podTerminate) & exec /start.sh'])
    # Execute the existing deadline hook locally; it only parses the string, no pod calls.
    result = subprocess.run([sys.executable, str(DEADLINE)], input=json.dumps(e), text=True, capture_output=True)
    assert result.returncode == 0
    assert gate.shape(e) == ('toy-evolution', 32, 0)
    for field in ('vcpuCount', 'computeType', 'gpuCount'):
        e['tool_input'][field] = e['tool_input']['body'].pop(field)
    assert gate.shape(e) == ('toy-evolution', 32, 0)
    e['tool_input'] = dict(name='theory-test', vcpuCount=16, computeType='GPU', gpuCount=2)
    assert gate.shape(e) == ('theory', 16, 2)


def test_nested_v2_cpu_gpu_shapes_and_conflicts():
    cpu = event(env={}, name='theory-cpu')
    cpu['tool_input']['body'] = {'name': 'theory-cpu', 'cpu': {'id': 'cpu3c', 'vcpuCount': 16}}
    assert gate.shape(cpu) == ('theory', 16, 0)
    gpu = event('orbits', computeType='GPU', gpuCount=2, gpu={'count': 2})
    assert gate.shape(gpu) == ('orbits', 32, 2)
    for e in (event(cpu={'vcpuCount': 16}), event(gpu={'count': 1}),
              event(cpu={}, gpu={})):
        with pytest.raises(ValueError):
            gate.shape(e)


def test_one_sanitized_line_per_create_noncreate_skipped(tmp_path):
    e = event()
    e['tool_input']['body']['env']['API_KEY'] = 'DO-NOT-LOG'
    e['tool_input']['body']['cmd'] = ['DO-NOT-LOG']
    for _ in range(3):
        assert gate.gate(e, tmp_path)[0] == 0
    assert gate.gate({'tool_name': 'mcp__runpod__list-pods'}, tmp_path) == (0, '')
    assert len(gate.AUDIT.read_text().splitlines()) == 3
    assert 'DO-NOT-LOG' not in gate.AUDIT.read_text()


def test_cli_json_additional_context_and_malformed_input(tmp_path):
    for payload in (json.dumps(event()), '{', 'null', '[]'):
        result = subprocess.run([sys.executable, str(HOOK)], input=payload, text=True, capture_output=True,
                                env=dict(os.environ, HOME=str(tmp_path), PYTHONPATH=str(ROOT)))
        assert result.returncode == 0 and not result.stderr
        output = json.loads(result.stdout)['hookSpecificOutput']
        assert output['hookEventName'] == 'PreToolUse'
        assert 'rh pods enqueue' in output['additionalContext']
    audit = tmp_path/'.local/state/research-harness/pod-gate-audit.jsonl'
    assert len(audit.read_text().splitlines()) == 4
