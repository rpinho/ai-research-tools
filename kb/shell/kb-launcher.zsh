# kb-launcher.zsh: every agent session gets its OWN sparse git worktree of the KB.
#
# Source this from ~/.zshrc:   source /path/to/your-kb/shell/kb-launcher.zsh
#
# Why: the main tree is ONE working tree + ONE index. Two agents in it at once clobber each
# other's whole-file writes to state.md / index.md. No setting can relocate a session after it
# starts (it is locked to its launch cwd, and AGENTS.md is read too late), so isolation has to
# happen at LAUNCH. These wrappers make the safe path the default path:
#
#   kb-claude            new session in a fresh worktree  ~/.worktrees/<kb>/<adjective-noun>/
#   kb-claude <name>     reopen (or create) the named worktree; reattach its tmux session if live
#   kb-codex [<name>]    same for Codex
#   kb-land              from inside a worktree: merge main in, then fast-forward main
#
# If you want plain `claude` / `codex` to do this, alias them (raw CLI stays `command claude`):
#   claude() { case "${1:-}" in -*|mcp|config|doctor|update) command claude "$@";; *) kb-claude "$@";; esac }
#
# Configure:
#   KB_REPO        the main tree (default ~/kb)
#   KB_SPARSE_SKIP space-separated top-level dirs to leave out of worktrees (default "inbox")
#   KB_ADD_DIRS    space-separated gitignored dirs in the MAIN tree to expose to the agent
#                  (private originals that exist only there; symlinked into the worktree)
#   KB_TMUX        set to 1 to run sessions inside tmux (survives SSH disconnects)

: "${KB_REPO:=$HOME/kb}"
: "${KB_SPARSE_SKIP:=inbox}"
: "${KB_ADD_DIRS:=}"

_kb_worktree() {  # $1 = name (optional) -> prints the worktree path, creating it if needed
  local repo="$KB_REPO" base="$HOME/.worktrees/${KB_REPO:t}"
  local adj=(amber brisk calm dapper eager fuzzy glad jolly keen lush merry nimble proud quiet ruby swift teal vivid witty zesty)
  local noun=(otter falcon maple cedar comet ember harbor lynx pine quartz reef sage tide vale willow yak zephyr badger crane delta)
  local name="${1:-${adj[RANDOM%${#adj[@]}+1]}-${noun[RANDOM%${#noun[@]}+1]}}"
  local wt="$base/$name"
  if [ ! -d "$wt" ]; then
    mkdir -p "$base"
    local -a patterns=('/*')
    local d; for d in ${=KB_SPARSE_SKIP}; do patterns+=("!/$d/"); done
    git -C "$repo" worktree add --no-checkout -b "$name" "$wt" main &&
    git -C "$wt" sparse-checkout set --no-cone "${patterns[@]}" &&
    git -C "$wt" checkout >/dev/null || { echo "worktree setup failed (branch name taken? re-run for a new name)" >&2; return 1; }
  fi
  # Gitignored private dirs exist only in the main tree: symlink them in (the link is ignored too).
  for d in ${=KB_ADD_DIRS}; do
    [ -e "$repo/$d" ] && mkdir -p "$wt/${d:h}" && ln -sfn "$repo/$d" "$wt/$d" 2>/dev/null
  done
  echo "$wt"
}

_kb_run() {  # $1 = claude|codex, $2 = optional worktree name
  local harness="$1" wt name session
  wt="$(_kb_worktree "${2:-}")" || return 1
  name="${wt:t}"; session="${harness}-${name}"
  local -a args
  if [ "$harness" = claude ]; then args=(--name "$name"); else args=(--cd "$wt"); fi
  if [ "${KB_TMUX:-}" = 1 ] && command -v tmux >/dev/null; then
    tmux has-session -t "=$session" 2>/dev/null && { tmux attach-session -t "=$session"; return; }
    # tmux runs the command through sh, so this is the real binary, not a shell function.
    tmux new-session -s "$session" -c "$wt" "$harness ${(q)args[@]}"
  else
    ( cd "$wt" && command "$harness" "${args[@]}" )
  fi
}

kb-claude() { _kb_run claude "${1:-}"; }
kb-codex()  { _kb_run codex  "${1:-}"; }

# Land THIS worktree's branch onto main: pull main in (resolve conflicts HERE), then fast-forward main.
kb-land() {
  local br; br="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)" || { echo "not a git repo"; return 1; }
  [ "$br" = "main" ] && { echo "on main: nothing to land"; return 1; }
  if git merge --no-edit main && git -C "$KB_REPO" merge --ff-only "$br"; then
    echo "landed '$br' -> main. When this session is done:"
    echo "  git -C '$KB_REPO' worktree remove '$PWD' && git -C '$KB_REPO' branch -d '$br'"
  else
    echo "conflict: resolve it here in this worktree, commit, then re-run kb-land"
  fi
}
