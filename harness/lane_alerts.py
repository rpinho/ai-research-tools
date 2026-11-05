"""Retryable lane-health alerts. Never accepts research text as a push payload.

Only the scheduler invokes monitor(); evaluation and CLI commands are read-only.
The state lock is nonblocking and unrelated to all lane/pod/worker locks.
"""
import fcntl
import storage
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import uuid


# Lane-health alerts belong to the agent responsible for recovery: they go to the AGENT
# queue, never to an operator's phone. A stalled lane is an agent's job to fix, not a
# notification for a human to read and ignore. Operator pushes are reserved for what only
# a human can act on (money, a decision, a deadline).
AGENT_QUEUE = Path('.runtime/agent-alerts.jsonl')


def queue_path():
    """Resolved at call time so tests (and isolated roots) never touch real state.

    RH_AGENT_ALERTS overrides the default; the test suite sets it to a temp file.
    """
    override = os.environ.get('RH_AGENT_ALERTS', '').strip()
    return Path(override).expanduser() if override else AGENT_QUEUE


def push(message):
    """Append to the agent queue. Returns True on success so retry logic still works."""
    try:
        AGENT_QUEUE = queue_path()
        AGENT_QUEUE.parent.mkdir(parents=True, exist_ok=True)
        import datetime as _dt
        row = json.dumps({'utc': _dt.datetime.now(_dt.timezone.utc).isoformat(timespec='seconds'),
                          'source': 'research-harness', 'kind': 'lane-health',
                          'message': message, 'for': 'agents'}, ensure_ascii=False)
        with AGENT_QUEUE.open('a') as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            stream.write(row + chr(10))
            stream.flush()
            os.fsync(stream.fileno())
        return True
    except OSError:
        return False


def pod_alert(root, row):
    """Wake the owning agents through both durable routes; never phone pushes.

    A receipt is committed by the caller only after both writes succeed. A
    failed queue append therefore retries on the next sample.
    """
    if (row.get('kind') not in ('pod-idle', 'pod-stalled')
            or not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', row.get('lane', ''))
            or not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', row.get('pod', ''))
            or not re.fullmatch(r'[a-z0-9]+', row.get('pod_id', ''))
            or row.get('for') != 'agents'):
        raise ValueError('invalid pod alert routing metadata')
    try:
        target = root / 'lanes' / row['lane'] / 'alerts' / (row['kind'] + '-' + row['pod'])
        target.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(row, sort_keys=True) + '\n'
        write(target, text)
        queue = queue_path()
        queue.parent.mkdir(parents=True, exist_ok=True)
        with queue.open('a') as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        return True
    except OSError:
        return False


def write(path, text):
    temporary = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    with temporary.open('w') as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(temporary), str(path))


def trash(path):
    """Keep recovery reversible using the configured system Trash command."""
    storage.trash(path)


def body(lane, checks, owner=None):
    # Every check name comes from code, never an error message or evidence file.
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', lane):
        raise ValueError('unsafe lane name')
    if any(not re.fullmatch(r'[a-z0-9][a-z0-9-]*', check) for check in checks):
        raise ValueError('unsafe check name')
    text = 'lane={}\nfailure_count={}\nchecks={}\n'.format(lane, len(checks), ','.join(sorted(checks)))
    if owner is not None:
        # Session names are routing metadata; never arbitrary descriptive text.
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}', owner):
            raise ValueError('unsafe owner_session')
        text += 'owner_session={}\n'.format(owner)
    return text


def update(root, channel, lane, failures, kind=None, owner=None):
    """fingerprint may hash diagnostics; alerts/state only store safe metadata."""
    state_dir = root / '.lane-health-state'
    state_path = state_dir / (channel + '-' + lane + '.json')
    try:
        state = json.loads(state_path.read_text())
        if not isinstance(state, dict):
            state = {}
    except (FileNotFoundError, ValueError):
        state = {}
    alerts = root / 'alerts'
    checks = [f['check'] for f in failures]
    message = body(lane, checks, owner)
    fingerprint = hashlib.sha256(json.dumps(sorted(set((f['check'], f.get('fingerprint', f['message'])) for f in failures))).encode()).hexdigest()
    active = bool(failures)
    changed = active and (not state.get('active') or fingerprint != state.get('fingerprint'))
    recovered = not active and state.get('active')
    if active:
        filename = ('lint-failed-' if channel == 'lint' else 'lane-' + kind + '-') + lane
        # Remove an obsolete cadence severity; a changed state gets its own alert.
        previous = state.get('alert')
        if previous and previous != filename:
            trash(alerts / previous)
        if changed or not (alerts / filename).exists():
            write(alerts / filename, message)
        if changed:
            state.update(active=True, fingerprint=fingerprint, alert=filename,
                         pending=message, pending_key=uuid.uuid4().hex)
    elif recovered:
        if state.get('alert'):
            trash(alerts / state['alert'])
        filename = ('lint-recovered-' if channel == 'lint' else 'lane-recovered-') + lane
        write(alerts / filename, message)
        state.update(active=False, alert=None, fingerprint=None,
                     pending=message, pending_key=uuid.uuid4().hex)
    if state:
        # Commit before calling the notifier. A failed push remains pending.
        write(state_path, json.dumps(state, sort_keys=True) + '\n')
        if state.get('pending') and push(state['pending']):
            state.pop('pending', None)
            state.pop('pending_key', None)
            write(state_path, json.dumps(state, sort_keys=True) + '\n')


def monitor(root, channel, rows):
    root = root.expanduser()
    state_dir = root / '.lane-health-state'
    state_dir.mkdir(parents=True, exist_ok=True)
    (root / 'alerts').mkdir(parents=True, exist_ok=True)
    code = 0
    with (state_dir / (channel + '.lock')).open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0  # Another poll owns alerts; never wait for it or block lane work.
        for row in rows:
            try:
                update(root, channel, row['lane'], row['failures'],
                       row.get('kind'), row.get('owner_session'))
            except (OSError, ValueError, subprocess.SubprocessError):
                print('FAIL {}: {} alert/state update failed'.format(row['lane'], channel))
                code = 1
            if row['failures']:
                code = 1
    return code
