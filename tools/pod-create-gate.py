#!/usr/bin/env python3
"""Local capacity receipt hook. Audit by default; enforcement is opt-in per lane."""
import datetime as dt
import fcntl
import json
import os
import math
import os
from pathlib import Path
import re
import sys
import time

HARNESS = Path(os.environ.get('RH_ROOT', str(Path(__file__).resolve().parents[1] / 'harness')))
CONFIG = Path(os.environ.get('RH_POD_GATE_CONFIG', str(HARNESS / 'pod-gate.json'))).expanduser()
AUDIT = Path(os.environ.get('RH_POD_GATE_AUDIT', str(HARNESS / '.runtime/pod-gate-audit.jsonl'))).expanduser()
LANES = frozenset(filter(None, os.environ.get('RH_POD_LANES', '').split(',')))
EXCLUDED = frozenset(filter(None, os.environ.get('RH_POLICY_EXCLUDED', '').split(',')))
sys.path.insert(0, str(HARNESS))


def policy():
    try:
        cfg = json.loads(CONFIG.read_text())
        if not isinstance(cfg, dict) or cfg.get('mode') not in ('audit', 'enforce'):
            raise ValueError('invalid mode')
        lanes = cfg.get('enforce_lanes', [])
        if not isinstance(lanes, list) or any(lane not in LANES | EXCLUDED for lane in lanes):
            raise ValueError('invalid lanes')
        return cfg['mode'], set(lanes) - EXCLUDED, None
    except FileNotFoundError:
        return 'audit', set(), None
    except (OSError, ValueError, TypeError):
        return 'audit', set(), 'invalid config; using audit'


def request_parts(event):
    """The deadline hook reads tool_input.body; also accept flattened MCP input."""
    inp = event.get('tool_input', {})
    if not isinstance(inp, dict):
        raise ValueError('invalid tool input')
    body = inp.get('body', inp)
    if not isinstance(body, dict):
        raise ValueError('invalid body')
    return body, inp


def infer_lane(event):
    body, inp = request_parts(event)
    explicit, named = set(), set()
    for part in (body, inp):
        env = part.get('env', {})
        if isinstance(env, list):
            env = {item.get('key', item.get('name')): item.get('value')
                   for item in env if isinstance(item, dict)}
        if isinstance(env, dict) and 'RH_LANE' in env:
            explicit.add(env['RH_LANE'])
        name = part.get('name', '')
        if isinstance(name, str):
            for lane in LANES | EXCLUDED:
                if re.search(r'(?:^|[^a-z0-9])' + re.escape(lane) + r'(?:$|[^a-z0-9])', name.lower()):
                    named.add(lane)
    if explicit:
        if len(explicit) != 1 or not explicit <= LANES | EXCLUDED:
            raise ValueError('invalid lane')
        if named and named != explicit:
            raise ValueError('conflicting lane declarations')
        return explicit.pop()
    if len(named) != 1:
        raise ValueError('missing or ambiguous lane')
    return named.pop()


def shape(event):
    body, inp = request_parts(event)
    lane = infer_lane(event)

    def field(key, default=None):
        if key in body and key in inp and body[key] != inp[key]:
            raise ValueError('conflicting resource declarations')
        return body.get(key, inp.get(key, default))

    cpu, gpu = field('cpu'), field('gpu')
    if cpu is not None and not isinstance(cpu, dict):
        raise ValueError('invalid cpu block')
    if gpu is not None and not isinstance(gpu, dict):
        raise ValueError('invalid gpu block')
    if cpu is not None and gpu is not None:
        raise ValueError('cpu and gpu blocks conflict')
    nested_vcpus = cpu.get('vcpuCount') if cpu is not None else None
    vcpus = field('vcpuCount', nested_vcpus)
    if nested_vcpus is not None and vcpus != nested_vcpus:
        raise ValueError('conflicting vCPU counts')
    if type(vcpus) not in (int, float) or not math.isfinite(vcpus) or vcpus <= 0:
        raise ValueError('explicit positive vcpuCount required')
    nested_compute = 'CPU' if cpu is not None else ('GPU' if gpu is not None else None)
    compute = field('computeType', nested_compute)
    if nested_compute is not None and compute != nested_compute:
        raise ValueError('conflicting compute types')
    if compute not in ('CPU', 'GPU'):
        raise ValueError('explicit computeType required')
    nested_count = gpu.get('count', 1) if gpu is not None else None
    count = field('gpuCount', nested_count if nested_count is not None else (1 if compute == 'GPU' else 0))
    if nested_count is not None and count != nested_count:
        raise ValueError('conflicting GPU counts')
    if type(count) is not int or count < 0 or (compute == 'GPU') != (count > 0):
        raise ValueError('computeType and gpuCount disagree')
    if compute == 'CPU' and (field('gpuTypeIds') or field('gpu')):
        raise ValueError('CPU request includes GPU fields')
    return lane, vcpus, count


def append_audit(row):
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(AUDIT), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, 'a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps(row, allow_nan=False, sort_keys=True) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def gate(event, root=HARNESS, now=None, malformed=False):
    if not malformed and isinstance(event, dict) and isinstance(event.get('tool_name'), str):
        if not re.search(r'(?:^|__)create[-_]pod$', event['tool_name']):
            return 0, ''
    now = time.time() if now is None else now
    mode, enforce_lanes, config_error = policy()
    lane, resources, status = None, None, 'unparsed'
    command = 'rh pods plan <lane> --vcpus N [--gpu]'
    try:
        lane = infer_lane(event)
        lane, vcpus, gpus = shape(event)
        resources = dict(vcpus=vcpus, gpus=gpus)
        command = 'rh pods plan {} --vcpus {:g}{}'.format(
            lane, vcpus, ' --gpu --gpus {}'.format(gpus) if gpus else '')
        if lane in EXCLUDED:
            status = 'excluded'
        else:
            import pods
            status = 'PASS' if pods.receipt_matches(Path(root), lane, vcpus, gpus, now) else 'missing_or_invalid'
    except Exception:
        # Never echo the input or exception: command payloads and env may contain keys.
        if resources is not None:
            status = 'unreadable'
    would_deny = lane not in EXCLUDED and status != 'PASS'
    deny = mode == 'enforce' and lane in enforce_lanes and would_deny
    row = dict(utc=dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat().replace('+00:00', 'Z'),
               lane=lane, shape=resources, receipt_status=status, mode=mode,
               would_enforce='deny' if would_deny else 'allow', decision='deny' if deny else 'allow')
    if config_error:
        row['config_status'] = config_error
    log_error = False
    try:
        append_audit(row)
    except Exception:
        log_error = True
    context = ('Pod capacity gate: {} mode; receipt {}. Enqueue runnable targets with '
               '`rh pods enqueue`, then run `{}` on the controller before creating the pod. '
               'Enforcement would {}.').format(mode, status, command, row['would_enforce'])
    if lane in EXCLUDED:
        context = 'Pod capacity gate: lane excluded; capacity enforcement is disabled by configuration.'
    if config_error:
        context += ' ' + config_error + '.'
    if log_error:
        context += ' WARNING: pod gate audit log could not be written.'
    return (2, 'Pod refused: ' + context) if deny else (0, context)


if __name__ == '__main__':
    try:
        event = json.load(sys.stdin)
    except (ValueError, TypeError):
        event = None
    code, message = gate(event, malformed=not isinstance(event, dict))
    if message:
        if code:
            print(message, file=sys.stderr)
        else:
            print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse',
                                                    'additionalContext': message}}))
    raise SystemExit(code)
