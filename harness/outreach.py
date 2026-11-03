"""Local outreach ledger and gates. This module never sends or fetches anything."""
import settings
import datetime as dt
import contextlib
import fcntl
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unicodedata
from urllib.parse import urlsplit
import uuid

CONTACTS = settings.path('RH_CONTACTS', Path('.runtime/contacts.json'))
VOICE_COMMAND = settings.command('RH_VOICE_COMMAND', 'voice-check')
SCIENCE_COMMAND = settings.command('RH_SCIENCE_CHECK_COMMAND', 'science-check')
CHANNELS = ('email', 'x_reply', 'x_post', 'x_dm', 'linkedin')
EMAIL = re.compile(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}')
URL = re.compile(r'\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s<>]+|\bwww\.[^\s<>]+')
UTC = dt.timezone.utc


def backup(path):
    """Keep the first preimage for this job, including empty preimages of new files."""
    dest = Path(str(path) + '.bak')
    if not dest.exists():
        if path.exists():
            shutil.copy2(path, dest)
        else:
            dest.touch(mode=0o600)


def time_of(value):
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timestamp must have a timezone')
    return parsed


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def safe(value, field):
    if not isinstance(value, str) or not value.strip() or '\n' in value or '\r' in value:
        raise ValueError(field + ' must be a nonempty single line')
    if EMAIL.search(value):
        raise ValueError(field + ': email addresses belong only in private contacts')
    return value.strip()


def settings(lane, rh):
    path = lane / 'OUTREACH.toml'
    cfg = rh.config(path) if path.exists() else {}
    for key, default in (('question_limit', 1), ('x_question_limit', 1), ('follow_up_days', 7)):
        cfg.setdefault(key, default)
        if type(cfg[key]) is not int or cfg[key] < 0 or (key == 'follow_up_days' and cfg[key] == 0):
            raise ValueError('OUTREACH ' + key + ' must be a positive integer (question_limit may be zero)')
    return cfg


def contact_key(name):
    contacts = json.loads(CONTACTS.read_text())
    exact = [r['name'] for r in contacts if r['name'].casefold() == name.casefold()]
    hits = exact or [r['name'] for r in contacts if name.casefold() in r['name'].casefold()]
    if len(hits) != 1:
        raise ValueError('email recipient must uniquely match a name in private contacts.json')
    return hits[0]


def current(lane, rh):
    return rh.latest(rh.rows(lane / 'OUTREACH.jsonl'))


def all_rows(root, rh):
    for lane in sorted((root / 'lanes').iterdir()):
        if lane.is_dir():
            yield from current(lane, rh).values()


def record(lane, row, rh):
    # Reject addresses even in optional metadata (quotes, summaries, reasons, paths).
    if EMAIL.search(json.dumps(row, ensure_ascii=False)):
        raise ValueError('ledger metadata contains an email address; use a private contacts name')
    path = lane / 'OUTREACH.jsonl'
    backup(path)
    rh.append(path, row)


def body(row):
    return Path(row['draft_path']).read_bytes().decode('utf-8')


def titles(text):
    text = re.sub(r'\[[^\]]+\]\([^\s)]+\)', ' LINK ', text)
    text = re.sub(r'["“][^"”]+["”]', ' TITLE ', text)
    return re.sub(r'\b(reading|your)\s+[^.!?\n]*?(?=\(https?://)', r'\1 TITLE ', text, flags=re.I)


def normalise(text, names=()):
    for name in sorted(names, key=len, reverse=True):
        for part in [name] + name.split():
            if len(part) > 1:
                text = re.sub(r'(?<!\w)' + re.escape(part) + r'(?!\w)', ' NAME ', text, flags=re.I)
    text = titles(text)
    text = URL.sub(' LINK ', text)
    text = unicodedata.normalize('NFKC', text).casefold()
    return ' '.join(re.findall(r'\w+', text))


def features(text, names=()):
    whole_body = text
    # Strip greetings and trailing signatures before choosing the boundary sentences.
    text = re.sub(r'^\s*(?:hi|hello|dear|hey|olá)\b[^\n]*\n+', '', text, flags=re.I)
    text = re.split(r'\n\s*(?:thanks|thank you|best|regards|cheers|obrigado|operator)(?:[,!\s]|$)', text, maxsplit=1, flags=re.I)[0]
    # Protect periods and question marks inside URLs before splitting sentences.
    text = URL.sub(' LINK ', titles(text))
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+|\n+', text) if s.strip()]
    first = normalise(sentences[0], names) if sentences else ''
    last = normalise(sentences[-1], names) if sentences else ''
    words = normalise(whole_body, names).split()
    return first, last, {tuple(words[i:i + 5]) for i in range(len(words) - 4)}


def jaccard(a, b):
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a or b else 0.0


def similarities(left, right):
    names = (left['to_name'], right['to_name'])
    a, b = features(body(left), names), features(body(right), names)
    scores = [jaccard(a[0].split(), b[0].split()), jaccard(a[1].split(), b[1].split()), jaccard(a[2], b[2])]
    return [(label, score) for label, score, limit in zip(('first', 'last', 'body-5gram'), scores, (.6, .6, .35))
            if score >= limit]


def recent_sent(row, days, clock):
    try:
        return bool(row.get('sent')) and dt.timedelta(0) <= clock - time_of(row['sent']['utc']) <= dt.timedelta(days=days)
    except (KeyError, ValueError, TypeError):
        return False


def collisions(row, everyone, clock):
    return [other for other in everyone if (other['lane'], other['id']) != (row['lane'], row['id']) and
            other.get('to_key', '').casefold() == row['to_key'].casefold() and recent_sent(other, 7, clock)]


def voice(text, channel):
    command = list(VOICE_COMMAND) + (['--reply'] if channel == 'x_reply' else [])
    try:
        run = subprocess.run(command, input=text, text=True, capture_output=True, timeout=30)
        fails = [l for l in run.stdout.splitlines() if l.startswith('FAIL:')]
        return fails or (['xlint could not complete'] if run.returncode else [])
    except (OSError, subprocess.TimeoutExpired):
        return ['xlint could not complete']


def science_checks(text, attachments):
    """Optional lane check: stdin JSON, stdout FAIL: lines, nonzero means refusal."""
    try:
        run = subprocess.run(SCIENCE_COMMAND, input=json.dumps({'text': text, 'attachments': [str(p) for p in attachments]}),
                             text=True, capture_output=True, timeout=30)
        errors = [line for line in run.stdout.splitlines() if line.startswith('FAIL:')]
        return errors or (['science checker could not complete'] if run.returncode else [])
    except (OSError, subprocess.TimeoutExpired):
        return ['science checker could not complete']


def link_errors(text):
    errors = []
    for match in URL.finditer(text):
        url = match.group().rstrip('.,;!?)]}')
        try:
            p = urlsplit(url)
            if p.scheme not in ('https', 'http') or not p.hostname or p.username or p.password or p.port == 0:
                raise ValueError()
            host = p.hostname.encode('idna').decode('ascii')
            if not re.fullmatch(r'[A-Za-z0-9.-]+|[0-9a-fA-F:]+', host) or '..' in host:
                raise ValueError()
        except (ValueError, UnicodeError):
            errors.append('malformed URL (not fetched)')
    # Catch incomplete schemes and spaces immediately following a scheme too.
    if re.search(r'https?:(?!//)|https?://(?:\s|$)', text, re.I):
        errors.append('malformed URL (not fetched)')
    return errors


def lint_rows(lane, selected, rh, allow_collision=None):
    cfg = settings(lane, rh)
    clock = time_of(rh.now())
    everyone = list(all_rows(lane.parent.parent, rh))
    peers = {r['id']: r for r in everyone if r['lane'] == lane.name and
             (recent_sent(r, 14, clock) or any(s.get('batch') and r.get('batch') == s['batch'] for s in selected))}
    peers.update({r['id']: r for r in selected})
    results = {}
    for row in selected:
        checks = dict(voice=[], questions=[], batch=[], contact=[], collision=[], links=[], science=[])
        try:
            text = body(row)
        except (OSError, UnicodeError):
            results[row['id']] = dict(ok=False, utc=rh.now(), checks={'draft': ['draft unreadable']})
            continue
        checks['voice'] = voice(text, row['channel'])
        limit = cfg['question_limit'] if row['channel'] in ('email', 'linkedin') else cfg['x_question_limit']
        questions = URL.sub('', text).count('?')
        if questions > limit:
            checks['questions'].append('at most {} question(s); found {}'.format(limit, questions))
        if row['channel'] == 'email':
            try:
                if contact_key(row['to_key']) != row['to_key']:
                    checks['contact'].append('to_key must be the canonical contacts name')
            except (OSError, ValueError, KeyError):
                checks['contact'].append('recipient missing from private contacts.json')
        for other in collisions(row, everyone, clock):
            if not allow_collision:
                checks['collision'].append('recent send: {}/{}'.format(other['lane'], other['id']))
        checks['links'] = link_errors(text)
        if cfg.get('science_checks', False) and row['channel'] == 'email':
            checks['science'] = science_checks(text, row.get('attachments', []))
        for other in peers.values():
            if other['id'] == row['id']:
                continue
            try:
                for label, score in similarities(row, other):
                    checks['batch'].append('pair {} / {}: {} Jaccard {:.3f}'.format(row['id'], other['id'], label, score))
            except (OSError, UnicodeError):
                checks['batch'].append('comparison draft unreadable: ' + other['id'])
        results[row['id']] = dict(ok=not any(checks.values()), utc=rh.now(), sha256=hashlib.sha256(text.encode('utf-8')).hexdigest(), checks=checks)
        if allow_collision:
            results[row['id']]['allow_collision'] = safe(allow_collision, 'collision reason')
    return results


def health(lane, rh):
    clock = time_of(rh.now())
    errors = []
    for row in current(lane, rh).values():
        approval = row.get('approval') or {}
        if row['status'] == 'approved':
            try:
                if clock - time_of(approval['utc']) > dt.timedelta(hours=48):
                    errors.append('outreach {}: approved over 48 h ago, never sent'.format(row['id']))
            except (KeyError, ValueError, TypeError):
                errors.append('outreach {}: invalid approval timestamp'.format(row['id']))
        if row['status'] == 'sent':
            if not all(approval.get(k) for k in ('utc', 'by', 'sha256', 'quote')):
                errors.append('outreach {}: sent without approval'.format(row['id']))
            try:
                if not row.get('reply') and time_of(row['follow_up_due']) < clock:
                    errors.append('outreach {}: follow-up overdue'.format(row['id']))
            except (KeyError, ValueError, TypeError):
                errors.append('outreach {}: invalid or missing follow-up date'.format(row['id']))
    return errors


@contextlib.contextmanager
def send_lock(root, action):
    # Serialize cross-lane check + receipt, while preserving the usual per-lane append lock.
    if action != 'sent':
        yield
        return
    with (root / '.outreach.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def arguments(sub):
    parser = sub.add_parser('outreach', help='draft, lint, approve and track outreach; never sends')
    actions = parser.add_subparsers(dest='action', required=True)
    for action in ('draft', 'lint', 'approve', 'sent', 'reply', 'close', 'drop', 'status', 'due'):
        p = actions.add_parser(action)
        if action == 'due':
            p.add_argument('--all', action='store_true', help='include future follow-ups')
            p.add_argument('--json', action='store_true')
            continue
        p.add_argument('lane', nargs='?' if action == 'status' else None)
        if action == 'status':
            p.add_argument('--json', action='store_true')
        elif action == 'draft':
            p.add_argument('--to', required=True)
            p.add_argument('--channel', required=True, choices=CHANNELS)
            p.add_argument('--purpose', required=True)
            p.add_argument('--file', required=True, type=Path)
            p.add_argument('--batch')
            p.add_argument('--attach', action='append', default=[], help='attached results, checked with the configured science command')
        elif action in ('lint', 'approve'):
            p.add_argument('id', nargs='?')
            p.add_argument('--batch')
            if action == 'lint':
                p.add_argument('--allow-collision')
            else:
                p.add_argument('--quote', required=True)
        else:
            p.add_argument('id')
            if action == 'sent':
                p.add_argument('--message-id', required=True)
                p.add_argument('--thread-id')
                p.add_argument('--via', required=True, choices=('api', 'browser'))
            elif action == 'reply':
                p.add_argument('--summary', required=True)
            else:
                p.add_argument('--reason', required=True)


def main(root, args, rh):
    root = root.expanduser().resolve()
    lane_name = getattr(args, 'lane', None)
    if lane_name and not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', lane_name):
        raise ValueError('invalid lane name')
    lane = root / 'lanes' / lane_name if lane_name else None
    if lane and not lane.is_dir():
        raise ValueError('lane directory missing')
    if args.action in ('status', 'due'):
        selected = list(current(lane, rh).values()) if lane else list(all_rows(root, rh))
        if args.action == 'due':
            clock = time_of(rh.now())
            selected = [r for r in selected if r['status'] == 'sent' and not r.get('reply') and
                        (args.all or time_of(r['follow_up_due']) < clock)]
        selected.sort(key=lambda r: (r['status'], r['lane'], r['id']))
        if args.json:
            print(json.dumps(selected, indent=2, ensure_ascii=False))
        else:
            for r in selected:
                print('{} {} {} {} {}{}'.format(r['status'], r['lane'], r['id'], r['to_name'], r['purpose'],
                      ' due=' + r['follow_up_due'] if r.get('follow_up_due') else ''))
        return 0
    with rh.locked(lane), send_lock(root, args.action):
        if args.action == 'draft':
            name = safe(args.to, 'recipient')
            key = contact_key(name) if args.channel == 'email' else name
            path = args.file.expanduser().resolve()
            row = dict(id=uuid.uuid4().hex[:16], lane=lane.name, channel=args.channel, to_name=name, to_key=key,
                       purpose=safe(args.purpose, 'purpose'), draft_path=str(path), draft_sha256=sha(path),
                       status='drafted', created=rh.now())
            if args.batch:
                row['batch'] = safe(args.batch, 'batch')
            if args.attach:
                row['attachments'] = [str(Path(p).expanduser().resolve()) for p in args.attach]
            record(lane, row, rh)
            print(row['id'])
            return 0
        have = current(lane, rh)
        if args.action in ('lint', 'approve'):
            if bool(args.id) == bool(args.batch):
                raise ValueError('select exactly one ID or --batch')
            selected = [r for r in have.values() if r.get('batch') == args.batch] if args.batch else [have[args.id]]
            if not selected:
                raise ValueError('empty batch')
        else:
            selected = [have[args.id]]
        if args.action == 'lint':
            results = lint_rows(lane, selected, rh, args.allow_collision)
            for old in selected:
                row = dict(old, lint=results[old['id']])
                # Auditing historical sends must never turn them into sendable drafts.
                if row['status'] in ('drafted', 'linted', 'approved'):
                    row['status'] = 'linted' if row['lint']['ok'] else 'drafted'
                    row.pop('approval', None)
                    if row['lint'].get('sha256'):
                        row['draft_sha256'] = row['lint']['sha256']
                record(lane, row, rh)
                print('{} {} {}'.format('PASS' if row['lint']['ok'] else 'FAIL', row['id'], row['to_name']))
                for check, failures in row['lint']['checks'].items():
                    for failure in failures:
                        print('  {}: {}'.format(check, failure))
            return int(not all(r['ok'] for r in results.values()))
        # Validate the entire batch before appending any approval.
        for row in selected:
            if args.action == 'approve':
                if row['status'] != 'linted' or not row.get('lint', {}).get('ok'):
                    raise ValueError('approval requires linted PASS')
                if sha(row['draft_path']) != row['lint']['sha256']:
                    raise ValueError('draft changed since lint; run outreach lint again')
                safe(args.quote, 'approval quote')
            elif args.action == 'sent':
                if row['status'] != 'approved' or not row.get('approval'):
                    raise ValueError('sent requires approved draft')
                if sha(row['draft_path']) != row['approval']['sha256']:
                    raise ValueError('draft changed after approval; lint and obtain approval again')
                if collisions(row, list(all_rows(root, rh)), time_of(rh.now())) and not row.get('lint', {}).get('allow_collision'):
                    raise ValueError('recent contact collision; lint with --allow-collision and obtain approval again')
            elif args.action == 'reply' and row['status'] != 'sent':
                raise ValueError('reply requires a sent message')
            elif args.action in ('close', 'drop') and row['status'] in ('closed', 'dropped'):
                raise ValueError('message already closed or dropped')
        for old in selected:
            row = dict(old, updated=rh.now())
            if args.action == 'approve':
                row.update(status='approved', approval=dict(utc=rh.now(), by='owner (relayed)', quote=args.quote,
                                                           sha256=sha(row['draft_path'])))
            elif args.action == 'sent':
                sent_at = time_of(rh.now())
                row.update(status='sent', sent=dict(utc=sent_at.isoformat(), message_id=safe(args.message_id, 'message id'), via=args.via),
                           follow_up_due=(sent_at + dt.timedelta(days=settings(lane, rh)['follow_up_days'])).isoformat())
                if args.thread_id:
                    row['sent']['thread_id'] = safe(args.thread_id, 'thread id')
            elif args.action == 'reply':
                summary = safe(args.summary, 'reply summary')
                if re.search(r'["“”«»]|^>|(?<!\w)\x27[^\x27\n]+\x27(?!\w)', summary):
                    raise ValueError('reply summary must paraphrase without quoted text')
                row.update(status='replied', reply=dict(utc=rh.now(), summary=summary))
            else:
                row.update(status='closed' if args.action == 'close' else 'dropped', reason=safe(args.reason, 'reason'))
            record(lane, row, rh)
            print('{} {}'.format(row['id'], row['status']))
    return 0
