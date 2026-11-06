#!/usr/bin/env bash
# SessionStart hook: inject a compact TITLE INDEX of the wiki into the session's context.
#
# Why: "read index.md first" is a prose rule, and it fails exactly the way prose rules fail. The
# typical miss: a truncated `grep -r ... | head` returns a few unrelated hits, and the agent
# answers "we never documented that" while a whole page on it sits in wiki/. index.md grows too
# big to inject, but the failure is narrow ("I did not know a page existed"), and TITLES are
# enough to prevent it. Regenerated every session, so it never goes stale.
#
# Fails open: any error prints nothing and exits 0. A broken hook must never block a session.
set -uo pipefail
cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0
[ -f scripts/kb-title-index.sh ] || exit 0
body=$(bash scripts/kb-title-index.sh 2>/dev/null) || exit 0
[ -z "$body" ] && exit 0
python3 - "$body" <<'PY' 2>/dev/null || exit 0
import json, sys
print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": sys.argv[1]}}))
PY
