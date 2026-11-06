#!/usr/bin/env bash
# kb-coherence.sh: weekly coherence report (scripts/coherence.py), run in a throwaway WORKTREE and landed.
#
# Why a worktree even for a tiny job: a scheduled job that commits in the MAIN tree is blocked by the
# pre-commit hook (rightly), leaves its files STAGED there, and the next morning the nightly cannot
# fast-forward main, so its branch never lands. Every scheduled writer works in a worktree and lands
# with the same guards as the nightly. (The nightly's self-heal lands anything this job strands.)
set -uo pipefail
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export KB_NIGHTLY=1
KB="${KB_ROOT:-$HOME/kb}"
d="$(date +%F)"
BR="nightly-coherence-$(date +%Y%m%d)"
WT="$HOME/.worktrees/$(basename "$KB")/$BR"

git -C "$KB" show-ref --quiet "refs/heads/$BR" && { echo "branch $BR exists; skipping"; exit 0; }
git -C "$KB" worktree add -q "$WT" -b "$BR" main || { echo "worktree add failed"; exit 1; }
cd "$WT" || exit 1

out="$(KB_ROOT="$WT" python3 scripts/coherence.py --date "$d")" || { echo "coherence.py failed; worktree left at $WT"; exit 1; }
echo "$out"
KB_ROOT="$WT" python3 scripts/day-ledger.py add --source manual --items 1 \
  --summary "Weekly coherence pass: $out" --pages "Coherence Report - $d"
git add "outbox/Coherence Report - $d.md" raw/_index/day-ledger.jsonl
git commit -q -m "coherence: weekly report $d" || echo "nothing to commit"

if [ -n "$(git -C "$KB" status --porcelain --untracked-files=no)" ]; then
  echo "main tree has uncommitted tracked changes; $BR left for the nightly's self-heal"; exit 1
fi
git merge -q --no-edit main && git -C "$KB" merge -q --ff-only "$BR" \
  && git -C "$KB" worktree remove --force "$WT" && git -C "$KB" branch -D "$BR" && echo "landed $BR" \
  || { echo "landing $BR failed; left for the nightly's self-heal"; exit 1; }
