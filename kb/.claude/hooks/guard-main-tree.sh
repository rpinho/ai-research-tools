#!/usr/bin/env bash
# guard-main-tree.sh: PreToolUse hook (Edit|Write|NotebookEdit). Works for Claude Code and Codex.
#
# Enforces the concurrency rule mechanically: agent edits to TRACKED files belong in a session
# worktree (edit there, then land), never in the MAIN tree. Also blocks CREATING new files inside
# tracked areas of the main tree. Stays out of the way everywhere else:
#   - any worktree (including ones that live inside the main tree's path) -> allowed
#   - gitignored paths (private originals you keep out of git)            -> allowed
#   - untracked scratch areas (inbox/, outbox/, settings.local.json)      -> allowed
#
# The main tree is discovered from git (the parent of the shared --git-common-dir), or set KB_MAIN.
# Exit 2 blocks the tool call and feeds stderr back to the model. Any internal error exits 0
# (fail open): a broken guard must never wedge a session.
set -u

input=$(cat)
fp=$(printf '%s' "$input" | python3 -c 'import json,sys
try:
    t = json.load(sys.stdin).get("tool_input", {})
    print(t.get("file_path") or t.get("notebook_path") or "")
except Exception:
    print("")' 2>/dev/null)
[ -z "$fp" ] && exit 0

here="${CLAUDE_PROJECT_DIR:-$(pwd)}"
MAIN="${KB_MAIN:-}"
if [ -z "$MAIN" ]; then
  common=$(git -C "$here" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || exit 0
  MAIN=$(dirname "$common")
fi
MAIN=$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$MAIN")
abs=$(python3 -c 'import os,sys; print(os.path.realpath(os.path.expanduser(sys.argv[1])))' "$fp")

case "$abs" in
  "$MAIN"/*) ;;            # inside the main tree's path: check below
  *) exit 0 ;;             # anywhere else (sibling worktrees, scratch dirs)
esac

# A worktree that lives inside the main tree's path has its own .git FILE. Walk up from the
# target to MAIN; if any directory on the way carries a .git file, it is a worktree: allow.
d=$(dirname "$abs")
while [ "$d" != "$MAIN" ] && [ "$d" != "/" ]; do
  [ -f "$d/.git" ] && exit 0
  d=$(dirname "$d")
done

rel=${abs#"$MAIN"/}

if git -C "$MAIN" ls-files --error-unmatch -- "$rel" >/dev/null 2>&1; then
  echo "BLOCKED by guard-main-tree: '$rel' is a TRACKED file in the MAIN tree ($MAIN). Tracked edits happen in a session worktree: edit there, then land onto main (AGENTS.md -> Concurrency). Untracked scratch areas and gitignored paths are not blocked." >&2
  exit 2
fi

if git -C "$MAIN" check-ignore -q -- "$rel" 2>/dev/null; then
  exit 0
fi

case "$rel" in
  wiki/*|raw/*|scripts/*|memory/*|templates/*|.agents/*|.claude/skills/*|state.md|index.md|log.md|goals.md|AGENTS.md|CLAUDE.md|CONVENTIONS.md|README.md)
    echo "BLOCKED by guard-main-tree: '$rel' would create a new file in a TRACKED area of the MAIN tree ($MAIN). Create it in a session worktree instead, then land." >&2
    exit 2 ;;
esac
exit 0
