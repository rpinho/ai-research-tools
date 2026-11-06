---
name: land
description: Land this session worktree's branch onto main (merge main in, resolve conflicts here, fast-forward main). Use when the human says "land", "merge" or "push to main".
---

# land: merge a worktree onto main, conflict-free

Every agent session runs in its own git worktree (see `AGENTS.md` -> Concurrency; the
`shell/kb-launcher.zsh` wrappers create them). When the human says **"land" / "merge" / "push to
main"**, do exactly this from inside the worktree, never a plain `git merge` / `git push` onto main.

1. **Confirm you are in a worktree, and find the main tree.**
   ```bash
   BR=$(git rev-parse --abbrev-ref HEAD)        # your session branch; if it is "main", STOP
   MAIN=$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")
   ```
   If `BR` is `main`, stop: there is nothing to land (you are in the main tree, not a worktree).

2. **Pull main into the worktree** and resolve conflicts *here*, where you have write access:
   ```bash
   git merge --no-edit main
   ```
   - `index.md` and `state.md` are **additive trackers**: on conflict, **keep BOTH sides'** content
     (merge the entries; never drop one side's edits).
   - `log.md` and `raw/_index/day-ledger.jsonl` are `merge=union` (`.gitattributes`), so they
     auto-merge; if they ever conflict, keep both.
   - For wiki/raw files, keep both sides unless it is genuinely the same edit.
   - Stage resolved files by **explicit path** (never `git add -A`) and commit the merge.

3. **Fast-forward main.** This is the only write to main, and it cannot conflict:
   ```bash
   git -C "$MAIN" merge --ff-only "$BR"
   ```
   If git refuses (main advanced again), re-run from step 2. If it refuses because the main tree
   has uncommitted tracked changes, report that and stop: never commit or stash in the main tree.

4. **Report success** and print the cleanup line for when the session is done:
   ```bash
   git -C "$MAIN" worktree remove "$PWD" && git -C "$MAIN" branch -d "$BR"
   ```

**Never** `git push`, **never** check out `main` in this worktree, **never** `git add -A`.

## Why this shape

`main` only ever *fast-forwards*, so the shared main tree never stops on a merge conflict: all
conflict resolution happens inside the disposable session worktree. With union merges for the
append-only files and "keep both" for the additive trackers, concurrent sessions cannot clobber
each other. The pre-commit hook (`.githooks/pre-commit`, check 1) refuses commits made in the main
tree, and the `guard-main-tree` hook refuses agent edits there, so the flow is enforced rather than
remembered.
