"""Read-only progress probes and agent-only pod stall evidence. No lifecycle calls."""
import base64
import datetime as dt
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
import time

import lane_alerts

MAX_SAMPLE_GAP = dt.timedelta(minutes=16)
REPEAT = dt.timedelta(minutes=30)
ACTION = 'stalled or idle: check the session, fix or end the pod'


def timestamp(value):
    clock = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if clock.tzinfo is None:
        raise ValueError('pod timestamp needs timezone')
    return clock


def validate(pod):
    for key in ('progress_path', 'setup_script'):
        value = pod.get(key)
        if value is not None and (not isinstance(value, str) or not value.startswith('/')
                or value == '/' or str(PurePosixPath(value)) != value
                or '..' in PurePosixPath(value).parts or value.startswith('//')
                or any(ord(c) < 32 or ord(c) == 127 for c in value)):
            raise ValueError(key + ' must be an absolute normalized path')
    minutes = pod.get('progress_stall_minutes', 15)
    if type(minutes) not in (int, float) or not math.isfinite(minutes) or minutes <= 0:
        raise ValueError('progress_stall_minutes must be positive and finite')


def progress_snapshot(path):
    """Runs in memory on the pod; missing files are an explicit observation."""
    try:
        value = Path(path).stat()
    except FileNotFoundError:
        return dict(exists=False, mtime_ns=None, age_seconds=None)
    if not stat.S_ISREG(value.st_mode):
        raise ValueError('progress_path must be a regular file')
    return dict(exists=True, mtime_ns=value.st_mtime_ns,
                age_seconds=max(0, (time.time_ns() - value.st_mtime_ns) / 1_000_000_000))


def progress_command(pod):
    # This is folded into the existing SSH measurement, never a second session.
    import inspect
    source = ('import json, stat, time\nfrom pathlib import Path\n'
              + inspect.getsource(progress_snapshot)
              + '\ntry:\n print(json.dumps(progress_snapshot(' + repr(pod['progress_path'])
              + ')))\nexcept (OSError, ValueError):\n print(json.dumps({"error": "unavailable"}))\n')
    encoded = base64.b64encode(source.encode()).decode()
    return 'python3 -B -c ' + shlex.quote('import base64; exec(base64.b64decode(' + repr(encoded) + '))')


def streak(recent, predicate):
    """Only adjacent measurement rows measure gaps; event rows never enter here."""
    result = []
    for row in reversed(recent):
        if not predicate(row):
            break
        if result and timestamp(result[-1]['utc']) - timestamp(row['utc']) > MAX_SAMPLE_GAP:
            break
        result.append(row)
    return list(reversed(result))


def span(rows):
    return ((timestamp(rows[-1]['utc']) - timestamp(rows[0]['utc'])).total_seconds() / 60
            if rows else 0)


def idle_rows(recent, pod):
    def idle(row):
        if row.get('reachable') is not True:
            return False
        if pod['gpus']:
            return (type(row.get('gpu_util')) in (int, float) and row['gpu_util'] == 0
                    and row.get('gpus', pod['gpus']) == pod['gpus'])
        cpu = row.get('cpu_frac')
        return type(cpu) in (int, float) and 0 <= cpu < .05
    return streak(recent, idle)


def stalled(recent, pod):
    if not pod.get('progress_path') or not recent:
        return None
    last = recent[-1]
    progress = last.get('progress', {})
    if (last.get('reachable') is not True or last.get('progress_path') != pod['progress_path']
            or not isinstance(progress, dict) or progress.get('error')
            or type(progress.get('exists')) is not bool):
        return None
    generation = progress.get('mtime_ns')
    if progress['exists'] and type(generation) is not int:
        return None
    matching = streak(recent, lambda r: r.get('reachable') is True
        and r.get('progress_path') == pod['progress_path']
        and isinstance(r.get('progress'), dict) and not r['progress'].get('error')
        and r['progress'].get('exists') == progress['exists']
        and r['progress'].get('mtime_ns') == generation)
    age = progress.get('age_seconds')
    # Existing file age is computed on the pod, avoiding controller/pod clock skew.
    minutes = max(span(matching), age / 60 if type(age) in (int, float) and math.isfinite(age) and age >= 0 else 0)
    if minutes < pod.get('progress_stall_minutes', 15):
        return None
    return dict(minutes=minutes, since=(timestamp(last['utc']) - dt.timedelta(minutes=minutes)).isoformat(),
                generation=generation)


def alert(root, recent, history, pod, rh):
    if not recent:
        return 0
    now = recent[-1]['utc']
    conditions = []
    idle = idle_rows(recent, pod)
    if len(idle) >= 2 and span(idle) >= (10 if pod['gpus'] else 20):
        conditions.append(('pod-idle', dict(minutes=span(idle), since=idle[0]['utc'])))
    progress = stalled(recent, pod)
    if progress:
        conditions.append(('pod-stalled', progress))
    failed = False
    for kind, evidence in conditions:
        episode = [kind, evidence['since']] if kind == 'pod-idle' else [
            kind, pod['progress_path'], evidence['generation'],
            evidence['since'] if evidence['generation'] is None else None]
        condition = hashlib.sha256(json.dumps(episode).encode()).hexdigest()
        # Exact identity prevents a replacement inheriting a throttle receipt.
        sent = [r for r in history if r.get('event') == 'AGENT_ALERT'
                and r.get('pod_id') == pod['pod_id'] and r.get('pod') == pod['name']
                and r.get('lane') == pod['lane'] and r.get('kind') == kind
                and r.get('condition') == condition]
        if sent and timestamp(now) - timestamp(sent[-1]['utc']) < REPEAT:
            continue
        minutes = evidence['minutes']
        payload = dict(utc=now, source='research-harness', kind=kind, **{'for': 'agents'},
                       lane=pod['lane'], pod=pod['name'], pod_id=pod['pod_id'],
                       dollars_per_hour=pod['dollars_per_hour'], **{'$/h': pod['dollars_per_hour']},
                       minutes_idle=minutes, idle_cost=round(minutes * pod['dollars_per_hour'] / 60, 4),
                       since_utc=evidence['since'], condition=condition, message=ACTION)
        if kind == 'pod-stalled':
            payload['minutes_stalled'] = minutes
        if lane_alerts.pod_alert(root, payload):
            event = dict(payload, event='AGENT_ALERT')
            rh.append(root / 'PODS.jsonl', event)
            history.append(event)
        else:
            failed = True
    return int(failed)


def shell_without_comments(text):
    """Keep newlines: shlex's comment reader otherwise eats command boundaries."""
    cleaned = []
    quote, escaped, comment = None, False, False
    previous = '\n'
    for char in text:
        if comment:
            if char == '\n':
                comment = False
                cleaned.append(char)
        elif escaped:
            cleaned.append(char)
            escaped = False
        elif char == '\\' and quote != "'":
            cleaned.append(char)
            escaped = True
        elif char == '#' and quote is None and (previous.isspace() or previous in ';&|()'):
            comment = True
        else:
            cleaned.append(char)
            if char == quote:
                quote = None
            elif quote is None and char in ('"', "'"):
                quote = char
        previous = char
    return ''.join(cleaned)


def setup_warnings(pod):
    path = pod.get('setup_script')
    if not path:
        return []
    try:
        text = Path(path).read_text()
    except (OSError, UnicodeError):
        return ['pod {} setup_script cannot be read: {}'.format(pod['name'], path)]
    warnings = []
    exported = False
    # Command boundaries prevent a later apt invocation's -y from masking an
    # unsafe earlier one. shlex excludes comments and preserves quoted values.
    lexer = shlex.shlex(shell_without_comments(text.replace('\\\n', ' ')),
                        posix=True, punctuation_chars=';&|(){}\n')
    lexer.commenters = ''
    lexer.whitespace = ' \t\r'
    lexer.whitespace_split = True
    commands, command = [], []
    try:
        for token in lexer:
            if all(c in ';&|(){}\n' for c in token):
                commands.append(command)
                command = []
            else:
                command.append(token)
        commands.append(command)
    except ValueError:
        return ['pod {} setup_script cannot be parsed'.format(pod['name'])]
    for tokens in commands:
        if not tokens:
            continue
        assignments = [t.split('=', 1)[1] for t in tokens if t.startswith('DEBIAN_FRONTEND=')]
        has_env = assignments[-1] == 'noninteractive' if assignments else exported
        # An export applies to following commands as well as the current one.
        if 'export' in tokens and assignments:
            exported = has_env
        if 'unset' in tokens and 'DEBIAN_FRONTEND' in tokens:
            exported = False
        for index, token in enumerate(tokens):
            if PurePosixPath(token).name != 'apt-get':
                continue
            args = tokens[index + 1:]
            if 'install' not in args:
                continue
            yes = any(t in ('--yes', '--assume-yes') or re.fullmatch(r'-[A-Za-z]*y[A-Za-z]*', t) for t in args)
            if not yes or not has_env:
                warnings.append('pod {} setup_script apt-get install requires -y and DEBIAN_FRONTEND=noninteractive'.format(pod['name']))
    return warnings


def warnings(entries):
    result = []
    for pod in entries:
        if pod['gpus'] and not pod.get('progress_path'):
            result.append('GPU pod {} has no progress_path; register a progress contract'.format(pod['name']))
        result.extend(setup_warnings(pod))
    return result
