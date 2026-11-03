#!/usr/bin/env python3
"""PreToolUse hook for email tools: configurable voice check and exact-byte outreach approval."""
import html
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import shlex
import sys

VOICE_COMMAND = shlex.split(os.environ.get('RH_VOICE_COMMAND', 'voice-check'))
BODY_KEYS = ('body', 'text', 'plainText', 'plain_text', 'message', 'content', 'htmlBody', 'html_body', 'html')
HARNESS = Path(os.environ.get('RH_OUTREACH_ROOT', str(Path.home() / '.local/share/research-harness')))


def approved_body(tool_input, root=None):
    """Match exact submitted UTF-8 bytes; voice's history/HTML cleanup is not approval."""
    values = [tool_input[k] for k in BODY_KEYS if isinstance(tool_input.get(k), str) and tool_input[k].strip()]
    if len(values) != 1:
        return False
    digest = hashlib.sha256(values[0].encode('utf-8')).hexdigest()
    for ledger in (root or HARNESS).glob('lanes/*/OUTREACH.jsonl'):
        try:
            latest = {}
            for line in ledger.read_text().splitlines():
                if line.strip():
                    row = json.loads(line)
                    latest[row['id']] = row
            for row in latest.values():
                approval = row.get('approval') or {}
                if row.get('status') == 'approved' and approval.get('sha256') == digest and approval.get('quote'):
                    if hashlib.sha256(Path(row['draft_path']).read_bytes()).hexdigest() == digest:
                        return True
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return False


def decision(reason, deny=True):
    if deny:
        print(json.dumps({'hookSpecificOutput': {
            'hookEventName': 'PreToolUse', 'permissionDecision': 'deny', 'permissionDecisionReason': reason}}))
    else:
        print(json.dumps({'systemMessage': 'WARNING: ' + reason}))


def body_of(tool_input):
    parts = []
    for key in BODY_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value)
    text = '\n'.join(parts)
    if '<' in text and '>' in text:
        text = html.unescape(re.sub(r'<[^>]+>', ' ', text))
    # Quoted history ("On ... wrote:" and "> " lines) is someone else's text, not ours.
    text = re.split(r'\n\s*On .{0,200}wrote:\s*\n', text, maxsplit=1)[0]
    return '\n'.join(l for l in text.splitlines() if not l.lstrip().startswith('>')).strip()


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    tool_input = event.get('tool_input') or {}
    text = body_of(tool_input)
    try:
        run = subprocess.run(VOICE_COMMAND, input=text, capture_output=True, text=True, timeout=30)
        fails = [l[5:].strip() for l in run.stdout.splitlines() if l.startswith('FAIL:')]
        if run.returncode and not fails:
            fails = ['voice checker unavailable']
    except (OSError, subprocess.TimeoutExpired):
        fails = ['voice checker unavailable']
    if fails:
        decision('Email voice gate (xlint): ' + '; '.join(fails)
                 + '. Rewrite and run the configured voice checker and retry.')
        return 0
    if not approved_body(tool_input):
        decision('Outreach body has no current exact-byte approval. Run: '
                 'rh outreach draft LANE --to NAME --channel email --purpose "what to learn" --file PATH; '
                 'rh outreach lint LANE ID; Operator reads it; '
                 'rh outreach approve LANE ID --quote "his own words". '
                 'After sending, run rh outreach sent LANE ID --message-id M --via api|browser.',
                 deny=os.environ.get('RH_OUTREACH_GATE', 'warn') != 'warn')
    return 0


if __name__ == '__main__':
    sys.exit(main())
