#!/usr/bin/env python3
"""PreToolUse hook: route direct conductor messages through the file inbox."""
import json
import os
from pathlib import Path
import re
import sys


def main():
    event = json.load(sys.stdin)
    if event.get("tool_name") != "SendMessage":
        return
    target = str((event.get("tool_input") or {}).get("to", "")).strip()
    name = os.environ.get("CONDUCTOR_NAME") or "conductor"
    root = Path(os.environ.get("CONDUCTOR_INBOX_DIR") or (
        Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
        / "conductor-inbox")).expanduser()
    try:
        address = (root / "conductor.addr").read_text(encoding="utf-8").strip()
    except OSError:
        address = ""
    socket_path = address[4:] if address.startswith("uds:") else address
    addressed = bool(socket_path and re.search(
        r"(?<![\w./-])" + re.escape(socket_path) + r"(?![\w./-])", target))
    if target.split(" [", 1)[0] == name or addressed:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"Use the {name} inbox: every wake replays the conductor's context. "
                'Run tell-conductor fyi "..." to file without a wake, or '
                'tell-conductor decision|human|break "..." to request one. '
                "Keep routine status in your own handoff.")}}))


if __name__ == "__main__":
    main()
