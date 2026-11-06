#!/bin/zsh
# Synchronous job runner: role selection, exit receipts and bounded capacity retries.
# usage: launch.sh JOB WORKDIR [--role ROLE] [--model MODEL --model-override REASON]
setopt pipefail
(( $# >= 2 )) || { print -u2 -- 'usage: launch.sh JOB WORKDIR'; exit 2; }
self=${0:A}
job=$1; dir=$2; shift 2
LAUNCH_ROLE=${LAUNCH_ROLE:-batch}; LAUNCH_MODEL=${LAUNCH_MODEL:-}; LAUNCH_MODEL_OVERRIDE=${LAUNCH_MODEL_OVERRIDE:-}
LAUNCH_EFFORT=${LAUNCH_EFFORT:-high}
backend=${AGENT_BACKEND:-codex}
[[ $backend == (codex|claude) ]] || { print -u2 -- 'AGENT_BACKEND must be codex or claude'; exit 2; }
[[ $LAUNCH_EFFORT == (low|medium|high|xhigh) ]] || { print -u2 -- 'bad LAUNCH_EFFORT'; exit 2; }
while (( $# )); do
  case $1 in
    --role) (( $# >= 2 )) || exit 2; LAUNCH_ROLE=$2; shift 2;;
    --model) (( $# >= 2 )) || exit 2; LAUNCH_MODEL=$2; shift 2;;
    --model-override) (( $# >= 2 )) || exit 2; LAUNCH_MODEL_OVERRIDE=$2; shift 2;;
    *) print -u2 -- "launch.sh: unknown argument $1"; exit 2;;
  esac
done
# Admission happens before registration, logs, model selection or the executor.
if [[ -n $LAUNCH_IDEA ]]; then
  idea_args=(ideas show "$LAUNCH_IDEA")
  [[ -z $LAUNCH_IDEA_ROOT ]] || idea_args=(--root "$LAUNCH_IDEA_ROOT" "${idea_args[@]}")
  idea_json=$(rh "${idea_args[@]}")
  idea_rc=$?
  if (( idea_rc != 0 )) || ! print -r -- "$idea_json" | python3 -I -c '
import json, sys
try:
    idea = json.load(sys.stdin)
    ok = (isinstance(idea, dict) and idea.get("status") == "testing"
          and isinstance(idea.get("id"), str) and idea["id"].startswith(sys.argv[1]))
except (ValueError, TypeError):
    ok = False
sys.exit(0 if ok else 1)
' "$LAUNCH_IDEA"; then
    print -u2 -- "launch.sh REFUSED $job: idea $LAUNCH_IDEA is not promoted (status testing). Run rh ideas promote first."
    exit 78
  fi
fi
[[ $dir = /* ]] || dir=${LAUNCH_WORK_ROOT:-$PWD}/$dir
cd "$dir" || exit 1
load=$(sysctl -n vm.loadavg 2>/dev/null | awk '{print int($2)}')
if [[ $load == <-> ]] && (( load > ${LAUNCH_MAX_LOAD:-30} )) && [[ -z $FORCE_LAUNCH ]]; then
  print -u2 -- "launch.sh refused: load $load"; exit 75
fi
artifacts=${LAUNCH_ARTIFACT_DIR:-$PWD/.runtime/jobs}
# Sweep passes a relative spec path; standalone jobs use the configured artifacts dir.
if [[ $job == */* ]]; then
  spec=${self:h}/$job.md; spec=${spec:A}
else
  spec=$artifacts/$job.md
fi
[[ -f $spec ]] || { print -u2 -- "missing spec: $spec"; exit 2; }
log=${spec:r}.log
# HARD RULE (Ricardo, 11-04): the goal must not turn into process. A spec over the cap needs
# SPEC_OVER_CAP="<reason>". The count is the job-specific part only (spec_words.py): the sweep
# embeds the lane's whole RULES.md and INTAKE.toml in its generated specs, which on 11-06 put
# c5's 472-word ask at 3,047 words and refused every job the sweep generated. Shared blocks
# added to the prompt below this line are never counted.
words=$(python3 "${self:h:h}/harness/tools/spec_words.py" "$spec") || exit 2
cap=${LAUNCH_SPEC_WORD_CAP:-1000}
if [[ -z $LAUNCH_RESUME ]] && (( words > cap )); then
  if [[ -z $SPEC_OVER_CAP ]]; then
    print -u2 -- "launch.sh REFUSED $job: spec is $words job-specific words, over the $cap cap (the goal is turning into process; set SPEC_OVER_CAP=reason)"
    print -- "exit 2 spec-over-cap $words words $(date)" >> "$log"
    exit 2
  fi
  print -- "spec over cap allowed ($words words): $SPEC_OVER_CAP $(date)" >> "$log"
fi
reg=${LAUNCH_STATE:-$PWD/.runtime/job-registry}
mkdir -p "$reg"
registry_name=${job//[^A-Za-z0-9_.-]/_}
print -l "dir=$dir" "pid=$$" > "$reg/$registry_name.job"
finished() {
  [[ ! -f $reg/$registry_name.job ]] || mv "$reg/$registry_name.job" "$reg/$registry_name.finished"
}
trap finished EXIT
stopped() { print -- "exit stopped by signal $(date)" >> "$log"; exit 143; }
trap stopped TERM HUP
if [[ $backend == codex ]]; then
  cx=${LAUNCH_CODEX:-codex}
else
  cx=${LAUNCH_CLAUDE:-claude}
fi
selector=${LAUNCH_SELECTOR:-${self:h:h}/harness/worker_models.py}
# The lane a job belongs to is its name up to the first separator (friends_new_x -> friends),
# overridable with LAUNCH_LANE. A missing GOAL.md or an unavailable `rh` is not fatal: a job
# that cannot read its goal still runs, and the gap shows up in the prompt's absence rather
# than as a dead lane.
lane=${LAUNCH_LANE:-${${job:t}%%[-_]*}}
lane_dir=${LAUNCH_LANE_ROOT:-${LAUNCH_IDEA_ROOT:-$PWD/.runtime}}/lanes/$lane
lessons=$(rh --root "${LAUNCH_LANE_ROOT:-${LAUNCH_IDEA_ROOT:-$PWD/.runtime}}" lessons for "$lane" -n "${LAUNCH_LESSONS:-12}" 2>/dev/null)
start=1; [[ -n $LAUNCH_RESUME ]] && start=2
for (( i = start; i <= 5; i++ )); do
  m=$(python3 "$selector" --role "$LAUNCH_ROLE" --executor "$backend" --model "$LAUNCH_MODEL" --model-override "$LAUNCH_MODEL_OVERRIDE" --log "$log" --receipt "$log.selection.json") || { print -- "exit 2 model-selection-refused $(date)" >> "$log"; exit 2; }
  # Shared context every job gets, injected here and never counted against the word cap above:
  # the job rules (prior art first), the lane's goal in the owner's words, and the lessons the
  # lane has already paid for. Before 11-06 only `rh ideas promote` enforced prior art and only
  # sweep prompts carried lessons, so 31 of the last 60 specs never mentioned either.
  { [[ ! -f ${self:h}/RULES.md ]] || { cat "${self:h}/RULES.md"; printf '\n---\n\n'; }
    [[ ! -f $lane_dir/GOAL.md ]] || { printf -- '--- LANE GOAL (%s, the owner'\''s words; agents never edit it) ---\n' "$lane"; cat "$lane_dir/GOAL.md"; printf '\n---\n\n'; }
    [[ -z $lessons ]] || { printf -- '--- LESSONS THIS LANE ALREADY PAID FOR ---\n%s\n---\n\n' "$lessons"; }
    cat "$spec"; (( i > 1 )) && printf '\n\nRESUME (attempt %d): read STATUS.md, REPORT.md and existing files; continue completed work.\n' $i; } > "$log.prompt"
  print -- "=== attempt $i model $m $(date)" >> "$log"
  attempt_log="$log.attempt-$i"
  nice -n 10 python3 "${self:h:h}/harness/agent_executor.py" --backend "$backend" \
    --executable "$cx" --directory "$dir" --model "$m" --effort "$LAUNCH_EFFORT" \
    --output "${spec:r}.last.md" --budget "${LAUNCH_CLAUDE_BUDGET:-1.00}" \
    < "$log.prompt" > "$attempt_log" 2>&1
  rc=$?
  cat "$attempt_log" >> "$log"
  print -- "exit $rc $(date)" >> "$log"
  python3 "$selector" --log "$log" --receipt "$log.selection.json" --outcome "$rc" || exit 2
  (( rc != 0 && i < 5 )) || break
  [[ $backend == codex ]] || break  # A Claude retry must not reset its spend cap.
  tail -40 "$attempt_log" | grep -q 'at capacity' || break
  sleep $(( ${LAUNCH_RETRY_SECONDS:-300} * i ))
done
exit "$rc"
