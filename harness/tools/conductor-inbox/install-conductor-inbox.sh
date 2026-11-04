#!/bin/sh
# Install for an explicit PID or the nearest ancestor with a local CLI socket.
exec python3 - "$0" "$@" <<'PY'
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys


def configured(key, fallback):
    return Path(os.environ.get(key) or fallback).expanduser().resolve()


def is_socket(path):
    try:
        return stat.S_ISSOCK(path.stat().st_mode)
    except OSError:
        return False


def install_link(source, destination):
    if destination.is_symlink() and destination.resolve() == source.resolve():
        return
    if destination.exists() or destination.is_symlink():
        raise SystemExit(f"refusing to replace an existing install path: {destination}")
    destination.symlink_to(source)


scripts = Path(sys.argv[1]).resolve().parent
if len(sys.argv) > 3:
    raise SystemExit("usage: install-conductor-inbox.sh [conductor-pid]")
socket_dir = configured("CONDUCTOR_SOCKET_DIR", "/tmp/cc-socks")
if len(sys.argv) == 3:
    if not sys.argv[2].isdigit() or int(sys.argv[2]) < 1:
        raise SystemExit("conductor PID must be a positive integer")
    socket = socket_dir / f"{sys.argv[2]}.sock"
else:
    pid = os.getppid()
    socket = socket_dir / f"{pid}.sock"
    visited = set()
    while pid > 1 and pid not in visited and not is_socket(socket):
        visited.add(pid)
        parent = subprocess.check_output(["ps", "-o", "ppid=", "-p", str(pid)], text=True).strip()
        pid = int(parent or "1")
        socket = socket_dir / f"{pid}.sock"
if not is_socket(socket):
    raise SystemExit("no conductor socket found; pass its PID or set CONDUCTOR_SOCKET_DIR")

home = Path.home()
root = configured("CONDUCTOR_INBOX_DIR", configured("XDG_STATE_HOME", home / ".local/state") / "conductor-inbox")
name = os.environ.get("CONDUCTOR_NAME") or "conductor"
human_kind = os.environ.get("CONDUCTOR_HUMAN_KIND") or "human"
watch_conf = configured("EVENT_WATCH_CONF", home / ".config/event-watch")
watch_state = configured("EVENT_WATCH_STATE", configured("XDG_STATE_HOME", home / ".local/state") / "event-watch")
for directory in (root / "wake", home / ".local/bin", home / ".claude/hooks", watch_conf):
    directory.mkdir(parents=True, exist_ok=True)
settings_path = home / ".claude/settings.json"
settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
pre = settings.setdefault("hooks", {}).setdefault("PreToolUse", [])
gate = home / ".claude/hooks/conductor-inbox-gate.py"
command = shlex.join(["env", f"CONDUCTOR_INBOX_DIR={root}", f"CONDUCTOR_NAME={name}",
                      sys.executable, str(gate)])
# Replace only our own named hook entry; leave all unrelated hooks intact.
entry = {"matcher": "SendMessage", "hooks": [
    {"type": "command", "command": command, "timeout": 10}]}
for i, existing in enumerate(pre):
    if any(str(gate) in h.get("command", "") for h in existing.get("hooks", [])):
        if len(existing.get("hooks", [])) != 1:
            raise SystemExit("inbox gate shares an entry with other hooks; separate that entry first")
        pre[i] = entry
        break
else:
    pre.append(entry)

install_link(scripts / "tell-conductor", home / ".local/bin/tell-conductor")
install_link(scripts / "tell-conductor", home / ".local/bin/tell-general")
install_link(scripts / "conductor-inbox-gate.py", gate)
(root / "conductor.addr").write_text(f"uds:{socket}\n")
settings_path.write_text(json.dumps(settings, indent=2) + "\n")
(watch_conf / "conductor-inbox.conf").write_text("new " + shlex.quote(str(root / "wake/*")) + "\n")

env = dict(os.environ, CONDUCTOR_INBOX_DIR=str(root), CONDUCTOR_NAME=name,
           EVENT_WATCH_CONF=str(watch_conf), EVENT_WATCH_STATE=str(watch_state))
for target in (name, f"uds:{socket}", str(socket)):
    result = subprocess.run([sys.executable, str(gate)], env=env, text=True,
                            input=json.dumps({"tool_name": "SendMessage", "tool_input": {"to": target}}),
                            capture_output=True, check=True)
    if json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] != "deny":
        raise SystemExit("installed conductor gate self-check failed")
# Establish the 'new' rule's baseline before peers start filing wake markers.
watcher = scripts.parents[2] / "tools/event-watch"
subprocess.run([sys.executable, str(watcher), "scan", "conductor-inbox"], env=env, check=True)
print("Conductor inbox installed. Export these values in peer and conductor shells:")
for key, value in (("CONDUCTOR_INBOX_DIR", str(root)), ("CONDUCTOR_NAME", name),
                   ("CONDUCTOR_HUMAN_KIND", human_kind), ("EVENT_WATCH_CONF", str(watch_conf)),
                   ("EVENT_WATCH_STATE", str(watch_state))):
    print(f"export {key}={shlex.quote(value)}")
print('export PATH="$HOME/.local/bin:$PATH"')
print("Read the inbox backlog, then run as a background task: event-watch wait conductor-inbox --max-hours 12")
PY
