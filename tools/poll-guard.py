#!/usr/bin/env python3
"""PreToolUse(Bash) hook: allow two repeated foreground status checks per 30 minutes. Background commands, mutations and # poll-ok bypass this advisory guard."""
import json
import os
import re
import sys
import time

STATE_DIR = os.path.expanduser(os.environ.get("POLL_GUARD_STATE", ".runtime/poll-guard"))
WINDOW_S = 30 * 60      # how long a fingerprint is remembered
FREE_CHECKS = 2         # identical checks allowed inside the window

# A command must look like one of these to be a "status check" at all.
STATUS = re.compile(
    r"(^|[;&|]\s*)(tail|cat|head|ls|stat|wc)\b[^|;&]*\.(log|out|err|json|flag|txt)"
    r"|\bssh\b[^|;&]*\b(cat|tail|ls|ps|nvidia-smi|squeue|qstat|du|df|wc|grep -c)\b"
    r"|\bget-pod\b|\bget-job-status\b|\blist-pods\b|\bendpoint-health\b"
    r"|\bevent-watch\s+(list|scan)\b"
    r"|\bps\b[^|;&]*\b(aux|-o)\b"
    r"|\bpsjobs\b",
    re.I,
)
# Anything that changes state is never a poll, whatever else it matches.
MUTATES = re.compile(
    r"\b(rm|mv|cp|mkdir|touch|tee|chmod|chown|kill|pkill|scp|rsync|git|pip|npm|make|"
    r"python3?\s+\S+\.py|nohup|launch\.sh|codex|claude|curl\s+-X|create-|delete-|update-|"
    r"run-endpoint|pod-action|submit)\b|>\s*\S|>>\s*\S",
    re.I,
)
NOISE = re.compile(r"\d+|'[^']*'|\"[^\"]*\"|/[A-Za-z0-9_.\-/]+")


def allow():
    sys.exit(0)


def deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def fingerprint(cmd):
    """Collapse literals so `tail -5 a.log` and `tail -20 a.log` are the same check."""
    return NOISE.sub("#", " ".join(cmd.split()))[:160]


def main():
    try:
        ev = json.load(sys.stdin)
    except Exception:
        allow()
    if ev.get("tool_name") != "Bash":
        allow()
    inp = ev.get("tool_input") or {}
    cmd = inp.get("command") or ""
    if inp.get("run_in_background") or "# poll-ok" in cmd:
        allow()
    if not STATUS.search(cmd) or MUTATES.search(cmd):
        allow()

    sid = re.sub(r"[^A-Za-z0-9_\-]", "", str(ev.get("session_id", "x")))[:64] or "x"
    path = os.path.join(STATE_DIR, sid + ".json")
    now = time.time()
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        try:
            seen = json.load(open(path))
        except Exception:
            seen = {}
        seen = {k: v for k, v in seen.items() if now - v[-1] < WINDOW_S}  # prune
        fp = fingerprint(cmd)
        hits = seen.setdefault(fp, [])
        hits.append(now)
        json.dump(seen, open(path, "w"))
    except Exception:
        allow()

    if len(hits) <= FREE_CHECKS:
        allow()

    mins = int((now - hits[0]) / 60)
    deny(
        f"[poll-guard] This is check #{len(hits)} of the same thing in {mins} min, and each one "
        "costs a full turn that re-reads your whole context. Don't look again — be told.\n"
        "Add what you are waiting for to $EVENT_WATCH_CONF/<job>.conf (new <glob> for a result, "
        "match <log> <regex> for errors or exit lines, stale <glob> <min> for a hang, pid <pid> for a "
        "crash, deadline <ISO>), then run `event-watch wait <job>` as a BACKGROUND Bash task "
        "(run_in_background: true). It blocks until something happens and the completion "
        "notification wakes you; re-run it after each exit. `event-watch --help` has the details.\n"
        "If you genuinely need this one look now, re-send the same command with `# poll-ok` appended."
    )


if __name__ == "__main__":
    main()
