#!/usr/bin/env python3
"""Check command text for a bounded provider self-termination marker.

A pod must carry its own kill in its start command, so it cannot outlive its deadline even if every
session dies. Recipe: docs/pods.md.
"""
import json, re, sys
MAX_SECONDS = 24 * 3600
b = json.load(sys.stdin).get('tool_input', {}).get('body', {})
start = ' '.join(map(str, [b.get('args', ''), *(b.get('entrypoint') or []), *(b.get('cmd') or [])]))
m = re.search(r'sleep\s+(\d+)', start)
if 'podTerminate' in start and m and int(m.group(1)) <= MAX_SECONDS:
    sys.exit(0)
print('Pod refused: its start command must end the pod itself. Set entrypoint ["/bin/bash","-c"] and cmd with '
      '(sleep SECONDS; curl podTerminate with $RUNPOD_API_KEY and $RUNPOD_POD_ID) & exec /start.sh, at most 24 h. '
      'Copy it from docs/pods.md.', file=sys.stderr)
sys.exit(2)
