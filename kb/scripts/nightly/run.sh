#!/usr/bin/env bash
# run.sh: scheduled agent run -> digest -> GUARANTEED delivery. Lab-agnostic, two-phase.
#
# A SKELETON with working control flow. What the agent should DO lives in prompt-ingest.md and
# prompt-write.md (marked YOUR SPEC); how the digest reaches you lives in notify.sh.
#
# Flow:
#   dated branch + throwaway sparse worktree (idempotent per day)
#   -> self-heal: land any stranded branch from an earlier day FIRST
#   -> probe primary backend through the SAME adapter + flags as the real run; failover if it fails
#   -> PHASE 1 ingest (orchestrator + cheap sub-agents) writes a compact brief, capped so the writer
#      starts by DIGEST_WRITER_START no matter what ingest is doing
#   -> PHASE 2 write (best writer, fresh session, reads only the brief); next backend if no digest
#   -> COMPILE: scripts/digest-lint.py; on failure ONE repair pass with the lint report; lint again
#   -> consume the intraday ledger (shell-side, only if the digest blended every entry in)
#   -> no digest? deterministic fallback (scripts/digest-fallback.py)
#   -> deliver (notify.sh digest); delivery failed? deliver a failure notice
#   -> land (merge main into the branch, fast-forward main), guarded; blocked? notice
#   An EXIT trap sends a notice if the script dies anywhere before delivery. Silence is not a
#   permitted outcome of this script.
#
# Test without delivering or landing:  NIGHTLY_TEST=1 scripts/nightly/run.sh
#
# This runs agents with permission prompts disabled (unattended). That is a real trade-off, accepted
# because the run is confined to a throwaway worktree, raw/ is the quarantine layer, and the prompt
# scopes the job. Know what you are agreeing to before you schedule it.
set -euo pipefail
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export KB_NIGHTLY=1        # the scheduled run CONSUMES the intraday ledger: exempt from pre-commit check 4

KB="${KB_ROOT:-$HOME/kb}"                       # the MAIN tree
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # prompts/adapter next to THIS script, so a worktree copy can be test-run
. "$HERE/models.env"
AR="$HERE/agent-run.sh"
NOTIFY="$HERE/notify.sh"
DAY="$(date '+%Y-%m-%d')"
YESTERDAY="$(date -v-1d '+%Y-%m-%d' 2>/dev/null || date -d yesterday '+%Y-%m-%d')"
TEST="${NIGHTLY_TEST:-}"
BR="nightly-$(date '+%Y%m%d')"; [ -n "$TEST" ] && BR="nightlytest-$(date '+%Y%m%d%H%M')"
WTBASE="$HOME/.worktrees/$(basename "$KB")"
WT="$WTBASE/$BR"
LOG="${NIGHTLY_LOG:-$HOME/.local/state/agent-kb/nightly.log}"
mkdir -p "$(dirname "$LOG")" "$WTBASE"
exec >>"$LOG" 2>&1
echo "=== $(date '+%F %T %Z') scheduled run ${TEST:+(TEST MODE)} ==="

notice() {   # notice SUBJECT BODY
  [ -n "$TEST" ] && { echo "(test mode: notice not sent) $1"; return 0; }
  "$NOTIFY" notice "$1" "$2" || echo "could not send notice: $1"
}

# ── DEATH IS NEVER SILENT. Every "guaranteed delivery" layer used to sit AFTER the model phases, so a
# crash between the digest commit and the send produced nothing at all. This fires on any non-zero exit
# before delivery and sends the tail of the log.
DELIVERED=0
on_exit() {
  local rc=$?
  [ "$rc" -eq 0 ] || [ "$DELIVERED" -eq 1 ] && return 0
  notice "KB scheduled run $DAY: DIED (exit $rc) before delivery" \
    "Branch $BR / worktree $WT may hold a written digest. Last log lines:
$(tail -n 25 "$LOG" 2>/dev/null)"
}
trap on_exit EXIT

land_branch() {   # land_branch BRANCH: merge main into it, ff main, clean up. Non-zero + untouched if unsafe.
  local br="$1" wt="$WTBASE/$1"
  if [ -d "$wt" ] && [ -n "$(git -C "$wt" status --porcelain --untracked-files=no)" ]; then
    echo "LAND $br: its worktree has uncommitted tracked changes; left in place"; return 1; fi
  if [ -n "$(git -C "$KB" status --porcelain --untracked-files=no)" ]; then
    echo "LAND $br: the MAIN tree has uncommitted tracked changes, so main cannot fast-forward:"
    git -C "$KB" status --short --untracked-files=no | sed 's/^/    /'; return 1; fi
  if [ -d "$wt" ]; then
    git -C "$wt" merge --no-edit main || { git -C "$wt" merge --abort 2>/dev/null; echo "LAND $br: merging main failed"; return 1; }
  fi
  git -C "$KB" merge --ff-only "$br" || { echo "LAND $br: fast-forward failed"; return 1; }
  [ -d "$wt" ] && git -C "$KB" worktree remove --force "$wt"
  git -C "$KB" branch -D "$br"; echo "landed + cleaned up $br"
}

model_for() {  # model_for BACKEND ROLE (INGEST | SUBAGENT | WRITER | PROBE)
  local v; v="$(printf '%s' "$1" | tr '[:lower:]' '[:upper:]')_$2_MODEL"; printf '%s' "${!v:-}"
}

probe_backend() {  # one tiny call through the SAME adapter and flags as the real run
  local b="$1" pf tl out attempt
  pf="$(mktemp -t probe.XXXXXX)"; tl="$(mktemp -t probe-log.XXXXXX)"; echo "Reply with exactly: OK" >"$pf"
  for attempt in 1 2 3; do
    if out="$("$AR" "$b" "$(model_for "$b" PROBE)" low 240 "$WT" "$pf" "$tl" 2>&1)" && grep -q "OK" <<<"$out"; then
      echo "probe $b: OK (attempt $attempt)"; rm -f "$pf" "$tl"; return 0; fi
    echo "probe $b attempt $attempt FAILED: $(printf '%s' "$out" | tail -c 300) | transcript: $(tail -c 400 "$tl")"
    [ "$attempt" -lt 3 ] && sleep 20
  done
  rm -f "$pf" "$tl"; return 1
}

build_prompt() {  # build_prompt TEMPLATE OUT BACKEND  (substitution in Python: no shell quoting hazards)
  NB_BACKEND="$3" NB_SUBAGENT="$(model_for "$3" SUBAGENT)" python3 "$HERE/build_prompt.py" "$1" "$2"
}

PHASE_RC=0
run_phase() {  # run_phase NAME BACKEND ROLE EFFORT TIMEOUT PROMPT_FILE; never aborts the script
  local model; model="$(model_for "$2" "$3")"
  echo "-- phase $1 on $2/$model started $(date '+%T')"
  "$AR" "$2" "$model" "$4" "$5" "$WT" "$6" "$(dirname "$LOG")/nightly-$1-$2.log" && PHASE_RC=0 || PHASE_RC=$?
  echo "-- phase $1 on $2/$model finished $(date '+%T') rc=$PHASE_RC"
  return 0
}

# ── Idempotence: a branch for today means today already ran (or a failed run awaits inspection).
if git -C "$KB" show-ref --quiet "refs/heads/$BR"; then echo "branch $BR exists; skipping. Inspect $WT"; exit 0; fi

# ── Self-heal: one blocked landing must never snowball into a week of unlanded work.
if [ -z "$TEST" ]; then
  for b in $(git -C "$KB" for-each-ref --format='%(refname:short)' 'refs/heads/nightly-*'); do
    [ "$b" = "$BR" ] && continue
    echo "stale branch $b found; landing it first"; land_branch "$b" || echo "stale branch $b could not land; left for attention"
  done
fi

# ── Sparse worktree (skip big untracked-by-design dirs). Never write into the live main tree.
git -C "$KB" worktree add --no-checkout "$WT" -b "$BR" main
git -C "$WT" sparse-checkout set --no-cone '/*' '!/inbox/'
git -C "$WT" checkout
cd "$WT"
BASE_SHA="$(git rev-parse HEAD)"

# ── Optional preflight: health checks that raise alerts (scripts/alert.py) the SAME night.
[ -n "${PREFLIGHT_CMD:-}" ] && { $PREFLIGHT_CMD || echo "preflight reported a problem (alert raised)"; }

# ── Backends: probe primary, failover if needed; pick the writer order.
PRIMARY="$NIGHTLY_BACKEND"; FAILOVER="$NIGHTLY_FAILOVER"
if ! probe_backend "$PRIMARY"; then
  if [ -n "$FAILOVER" ] && [ "$FAILOVER" != "$PRIMARY" ] && probe_backend "$FAILOVER"; then
    echo "primary $PRIMARY unavailable; running on $FAILOVER"; PRIMARY="$FAILOVER"; FAILOVER=""
  else
    echo "no backend passed the probe; skipping model phases (the fallback digest will be delivered)"; PRIMARY=""
  fi
fi
[ "$FAILOVER" = "$PRIMARY" ] && FAILOVER=""
WRITER_ORDER="$PRIMARY $FAILOVER"
if [ -n "$PRIMARY" ] && [ -n "$NIGHTLY_WRITER" ] && [ "$NIGHTLY_WRITER" != "$PRIMARY" ] && probe_backend "$NIGHTLY_WRITER"; then
  WRITER_ORDER="$NIGHTLY_WRITER $PRIMARY"
fi
echo "backends: primary=${PRIMARY:-none} writer order: ${WRITER_ORDER:-none}"

DIGEST="$WT/outbox/Digest - $DAY.html"
BRIEF="$WT/raw/_index/digest-brief-$DAY.md"
PENDING_N="$(KB_ROOT="$WT" python3 "$WT/scripts/day-ledger.py" count 2>/dev/null || echo 0)"
LEDGER_PENDING="$(mktemp -t ledger-pending.XXXXXX)"
KB_ROOT="$WT" python3 "$WT/scripts/day-ledger.py" pending --json >"$LEDGER_PENDING" 2>/dev/null || echo "[]" >"$LEDGER_PENDING"
echo "intraday ledger: $PENDING_N pending"
DELIVERY_STEP="DELIVERY: commit the digest file, then stop. The wrapper delivers it. Put the subject line in the HTML as <meta name=\"subject\" content=\"...\">: the actual read in under 90 characters, never a generic label."
export NB_DAY="$DAY" NB_YESTERDAY="$YESTERDAY" NB_PENDING_N="$PENDING_N" NB_DELIVERY="$DELIVERY_STEP" NB_TEST="$TEST"

WRITER_BACKEND=""
if [ -n "$PRIMARY" ]; then
  # PHASE 1: ingest, capped so the writer starts on time (ingest is optional work; the digest is the deliverable)
  P1="$(mktemp -t p1.XXXXXX)"
  if build_prompt "$HERE/prompt-ingest.md" "$P1" "$PRIMARY"; then
    now_s=$(date +%s)
    start_s=$(date -j -f "%Y-%m-%d %H:%M" "$DAY $DIGEST_WRITER_START" +%s 2>/dev/null || date -d "$DAY $DIGEST_WRITER_START" +%s 2>/dev/null || echo $((now_s + INGEST_TIMEOUT)))
    cap=$(( start_s - now_s )); [ "$cap" -lt 600 ] && cap=600
    [ "$cap" -lt "$INGEST_TIMEOUT" ] && ING_T="$cap" || ING_T="$INGEST_TIMEOUT"
    echo "ingest budget: ${ING_T}s (writer must start by $DIGEST_WRITER_START)"
    run_phase ingest "$PRIMARY" INGEST "$INGEST_EFFORT" "$ING_T" "$P1"
  else
    echo "ingest prompt failed to build; skipping phase 1"
  fi
  [ -s "$BRIEF" ] && echo "brief: $(wc -w <"$BRIEF" | tr -d ' ') words" || echo "PHASE 1 WROTE NO BRIEF; the writer reconstructs from git log"

  # Corrections still unpropagated after phase 1 go into the brief, and the digest must mention them.
  CORR_OUT="$(KB_ROOT="$WT" python3 "$WT/scripts/coherence.py" --corrections-only 2>&1)" && CORR_RC=0 || CORR_RC=$?
  if [ "$CORR_RC" -eq 1 ]; then
    { echo; echo "## Corrections not yet propagated (coherence check 5): the digest must list these"; echo "$CORR_OUT"; } >>"$BRIEF"
  fi

  # PHASE 2: write, failing over to the next backend if no digest appears
  for WB in $WRITER_ORDER; do
    P2="$(mktemp -t p2.XXXXXX)"
    build_prompt "$HERE/prompt-write.md" "$P2" "$WB" || { echo "write prompt failed to build for $WB"; continue; }
    run_phase write "$WB" WRITER "$WRITER_EFFORT" "$WRITER_TIMEOUT" "$P2"
    if [ -s "$DIGEST" ]; then WRITER_BACKEND="$WB"; break; fi
    echo "phase write on $WB produced no digest"
  done
fi

# ── COMPILE: lint, ONE repair pass, lint again. Delivered even if it still fails (never silence).
LINT_EXTRA=()
[ "${CORR_RC:-0}" -eq 1 ] && LINT_EXTRA+=(--must-mention '[Cc]orrection')
# Add deterministic must-mentions here, e.g. "a source you always want summarized posted tonight":
#   if <detector>; then LINT_EXTRA+=(--must-mention 'Their Name'); fi
if [ -s "$DIGEST" ]; then
  LINT_OUT="$(python3 "$WT/scripts/digest-lint.py" --ledger-pending "$LEDGER_PENDING" ${LINT_EXTRA[@]+"${LINT_EXTRA[@]}"} "$DIGEST" 2>&1)" && LINT_RC=0 || LINT_RC=$?
  echo "digest-lint pass 1: rc=$LINT_RC"; echo "$LINT_OUT"
  if [ "$LINT_RC" -ne 0 ] && [ -n "${WRITER_BACKEND:-$PRIMARY}" ]; then
    RP="$(mktemp -t repair.XXXXXX)"
    cat >"$RP" <<REPAIR_EOF
Repair the digest at "outbox/Digest - $DAY.html" in this worktree so it passes scripts/digest-lint.py.
Fix every HARD FAIL below. Cut, never pad; keep every link; keep the section order. If a fail is an Ask
requesting permission for routine work you already have standing permission for, DO that work now and
delete the Ask. Then re-run the lint yourself until it passes, and commit the file.

$LINT_OUT
REPAIR_EOF
    run_phase repair "${WRITER_BACKEND:-$PRIMARY}" WRITER "$REPAIR_EFFORT" "$REPAIR_TIMEOUT" "$RP"
    LINT_OUT="$(python3 "$WT/scripts/digest-lint.py" --ledger-pending "$LEDGER_PENDING" ${LINT_EXTRA[@]+"${LINT_EXTRA[@]}"} "$DIGEST" 2>&1)" && LINT_RC=0 || LINT_RC=$?
    echo "digest-lint after repair: rc=$LINT_RC"; echo "$LINT_OUT"
    [ "$LINT_RC" -ne 0 ] && echo "DIGEST STILL FAILS LINT; delivering anyway. Tighten the prompt or the repair."
  fi
  git add "outbox/Digest - $DAY.html" && git commit -qm "nightly: digest $DAY (post-lint)" || true
fi

# ── Consume the intraday ledger: shell-side, only if every entry made it in. Otherwise they roll over.
if [ "${PENDING_N:-0}" -gt 0 ] && [ -s "$DIGEST" ]; then
  OK="$(python3 "$WT/scripts/digest-lint.py" --json --ledger-pending "$LEDGER_PENDING" "$DIGEST" 2>/dev/null \
        | python3 -c 'import json,sys; print(int(json.load(sys.stdin).get("ledger_ok", False)))' 2>/dev/null || echo 0)"
  if [ "$OK" = 1 ]; then
    KB_ROOT="$WT" python3 "$WT/scripts/day-ledger.py" consume --digest "$DAY"
    git add raw/_index/day-ledger.jsonl && git commit -qm "nightly: consumed $PENDING_N intraday entries into digest $DAY" || true
  else
    echo "intraday ledger NOT consumed: some entries missing from the digest; they roll to tomorrow"
  fi
fi

# ── GUARANTEED DELIVERY. Every branch delivers something, or delivers a notice that it could not.
if [ ! -s "$DIGEST" ]; then
  echo "model produced NO digest; building the deterministic fallback"
  KB_ROOT="$WT" python3 "$WT/scripts/digest-fallback.py" --date "$DAY" --out "$DIGEST" || echo "fallback builder ALSO failed"
  [ -s "$DIGEST" ] && git add "outbox/Digest - $DAY.html" && git commit -qm "nightly: fallback digest $DAY" || true
fi
SUBJECT="$(sed -n 's/.*<meta name="subject" content="\([^"]*\)".*/\1/p' "$DIGEST" 2>/dev/null | head -1)"
SUBJECT="${SUBJECT:-KB digest $DAY}"
if [ -n "$TEST" ]; then
  echo "test mode: not delivering; digest at $DIGEST"
elif [ -s "$DIGEST" ]; then
  "$NOTIFY" digest "$DIGEST" "$SUBJECT" && echo "digest delivered" \
    || notice "KB digest $DAY: DELIVERY FAILED" "The digest was written ($DIGEST) but delivery failed. See $LOG."
else
  notice "KB digest $DAY: NO DIGEST PRODUCED" "Neither the model nor the fallback produced a digest. See $LOG."
fi
DELIVERED=1   # from here on, failures get their own notices; the death trap stands down

# ── Safety net: commit leftovers by explicit path, then land (guarded).
if [ -n "$(git status --porcelain)" ]; then
  echo "leftovers found; safety-committing"
  git add outbox raw wiki log.md state.md index.md 2>/dev/null || true
  git commit -qm "nightly: safety-commit of leftovers" || true
fi
if [ -n "$TEST" ]; then
  echo "test mode: not landing; inspect $WT (branch $BR)"
elif ! land_branch "$BR"; then
  notice "KB scheduled run $DAY: LANDING BLOCKED" "The digest was delivered, but $BR could not land on main (reason in $LOG). Tomorrow's run retries it first."
fi
echo "=== done $(date '+%T') ==="
