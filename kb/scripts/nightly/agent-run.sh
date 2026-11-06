#!/usr/bin/env bash
# agent-run.sh: run ONE headless agent turn on either lab, behind one interface.
#
#   agent-run.sh <backend> <model> <effort> <timeout> <workdir> <prompt_file> <transcript_log>
#
# backend = claude | codex. Prints the agent's final message to stdout; the full transcript goes to
# <transcript_log> (overwritten). Exit code is the agent's, or 142 on timeout.
#
# timeout = seconds (hard wall clock: right for a digest that must exist by morning), or
#           idle:<seconds> (no wall clock; stop only if the transcript stops growing that long).
#           Long builds were killed near the finish by wall clocks somebody guessed; a stalled
#           transcript is the real signal of a hang.
#
# Why one adapter: the auth probe, the real run and the repair pass must all go through the SAME
# code path with the SAME flags. A probe on a different path manufactures false diagnoses.
#
# AUTH under a scheduler (launchd/cron) is not your interactive login. Interactive keychain sessions
# can expire with no one there to refresh them. Use a long-lived token for unattended runs and point
# CLAUDE_TOKEN_FILE at it (a 0600 file outside the repo). Capture stdout AND stderr: some CLIs print
# auth errors on stdout, and a probe that discards it logs "<no stderr>" for weeks.
set -uo pipefail
[ $# -eq 7 ] || { echo "usage: agent-run.sh backend model effort timeout workdir prompt_file transcript_log" >&2; exit 2; }
backend=$1 model=$2 effort=$3 timeout=$4 wd=$5 pf=$6 tlog=$7
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/models.env"
[ -s "$pf" ] || { echo "agent-run: prompt file missing or empty: $pf" >&2; exit 2; }

# macOS has no coreutils `timeout`; perl alarm + exec keeps the pid so the signal reaches the agent.
with_timeout() {
  case "$timeout" in
    idle:*)
      local idle=${timeout#idle:}
      "$@" <&0 & local pid=$!   # explicit <&0: a background job otherwise gets /dev/null as stdin
      ( while kill -0 "$pid" 2>/dev/null; do sleep 60
          [ -f "$tlog" ] || continue
          age=$(( $(date +%s) - $(stat -f %m "$tlog" 2>/dev/null || stat -c %Y "$tlog") ))
          if [ "$age" -gt "$idle" ]; then echo "agent-run: no progress for ${age}s, stopping" >>"$tlog"
            kill -ALRM "$pid" 2>/dev/null; sleep 20; kill -9 "$pid" 2>/dev/null; fi
        done ) & local watcher=$!
      wait "$pid"; local r=$?; kill "$watcher" 2>/dev/null; wait "$watcher" 2>/dev/null; return "$r" ;;
    *) perl -e 'alarm shift; exec @ARGV' "$timeout" "$@" ;;
  esac
}

case "$backend" in
  claude)
    [ -n "${CLAUDE_TOKEN_FILE:-}" ] && [ -r "$CLAUDE_TOKEN_FILE" ] && export CLAUDE_CODE_OAUTH_TOKEN="$(cat "$CLAUDE_TOKEN_FILE")"
    unset CLAUDECODE CLAUDE_CODE_ENTRYPOINT          # never inherit an interactive session's identity
    [ -n "${CLAUDE_SUBAGENT_MODEL_ID:-}" ] && export CLAUDE_CODE_SUBAGENT_MODEL="$CLAUDE_SUBAGENT_MODEL_ID"
    ( cd "$wd" && with_timeout claude -p "$(cat "$pf")" --model "$model" --effort "$effort" \
        --dangerously-skip-permissions </dev/null ) >"$tlog" 2>&1
    rc=$?
    tail -c 6000 "$tlog"
    ;;
  codex)
    last="$(mktemp -t agent-run-last.XXXXXX)"
    # --ignore-user-config: interactive plugins, notify hooks and slow-starting MCP servers do not
    # belong in an unattended run. Auth still comes from CODEX_HOME.
    with_timeout codex exec --ignore-user-config --skip-git-repo-check --cd "$wd" \
        -m "$model" -c "model_reasoning_effort=\"$effort\"" \
        --dangerously-bypass-approvals-and-sandbox --color never -o "$last" - <"$pf" >"$tlog" 2>&1
    rc=$?
    cat "$last" 2>/dev/null; rm -f "$last"
    ;;
  *) echo "agent-run: unknown backend '$backend'" >&2; exit 2 ;;
esac
[ "$rc" -eq 142 ] && echo "agent-run: TIMED OUT (${timeout}) ($backend/$model)" >&2
exit "$rc"
