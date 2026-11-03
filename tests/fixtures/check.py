"""Deterministic synthetic checker. A test fixture, never a production voice policy."""
import json
import sys

text = sys.stdin.read()
if sys.argv[1] == 'voice':
    if '--reply' in sys.argv and len(text) > 240:
        print('FAIL: fixture reply longer than 240 characters')
        raise SystemExit(1)
    if 'Hello!' in text:
        print('FAIL: fixture greeting')
        raise SystemExit(1)
elif sys.argv[1] == 'science':
    payload = json.loads(text)
    if payload['attachments'] and 'useful or slop' not in payload['text']:
        print('FAIL: fixture attachment needs useful or slop question')
        raise SystemExit(1)
