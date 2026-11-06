#!/usr/bin/env bash
# kb-encrypted-backup.sh: client-side-encrypted snapshots of the KB to a synced folder.
#
# The live repo stays LOCAL. Never put a live .git inside a sync client's folder (iCloud, Drive,
# Dropbox): sync clients and git's lock files do not get along. This writes only AES-256
# CIPHERTEXT to $DEST, and the sync client carries it off-machine.
#
# Each run: a git bundle (all history) + a working-tree tarball (also catches gitignored private
# files that exist only on this disk), both encrypted, then a prune to the newest $KEEP of each.
#
# LESSONS BAKED IN
#   * Media is never backed up by default. Audio/video by extension, and any file over
#     $BIG_FILE_LIMIT_MB, are excluded, and every excluded big file is LISTED in the log so nothing
#     disappears silently. A single folder of screen recordings once made a nightly tarball 60 GB
#     and the next run died on a full disk.
#   * Count what you matched. A prune logged OK nightly for weeks and had never deleted anything (a
#     glob matched nothing, over a path with a space). The prune below logs its match count and
#     treats ZERO matches as a loud warning, not a quiet success.
#   * tar exit 1 = "file changed as we read it" (the KB is live): keep the archive. >1 = real error.
#   * An ERR trap logs the failing line, so a mid-run death is never just a header in the log.
#
# The passphrase file is yours to create (never in the repo, never in a transcript):
#   mkdir -p ~/.config/agent-kb && printf '%s' 'LONG-PASSPHRASE' > ~/.config/agent-kb/backup-passphrase
#   chmod 600 ~/.config/agent-kb/backup-passphrase      # and keep a copy in your secrets manager
# Restore: gpg -d personal-repo-<ts>.bundle.gpg > kb.bundle && git clone kb.bundle kb-restored
#
# Env: REPO, DEST, STAGING, PASS_FILE, KEEP, BIG_FILE_LIMIT_MB, EXCLUDES (space-separated ./paths)
set -euo pipefail
set -E

REPO="${REPO:-$HOME/kb}"
DEST="${DEST:?set DEST to the synced folder that receives ciphertext}"
STAGING="${STAGING:-$HOME/.kb-backup-staging}"
PASS_FILE="${PASS_FILE:-$HOME/.config/agent-kb/backup-passphrase}"
KEEP="${KEEP:-8}"
BIG_FILE_LIMIT_MB="${BIG_FILE_LIMIT_MB:-200}"
EXCLUDES="${EXCLUDES:-./inbox}"
PREFIX="${PREFIX:-kb}"
LOG="${LOG:-$STAGING/backup.log}"
MEDIA_EXCLUDES=()
for ext in mp4 MP4 mov MOV m4v webm mkv avi mp3 MP3 wav WAV m4a aac flac ogg opus; do MEDIA_EXCLUDES+=(--exclude="*.$ext"); done

command -v gpg >/dev/null || { echo "ERROR: gpg not found" >&2; exit 1; }
[ -e "$REPO/.git" ] || { echo "ERROR: $REPO is not a git repo" >&2; exit 1; }
[ -f "$PASS_FILE" ] || { echo "ERROR: passphrase file not found: $PASS_FILE (see header)" >&2; exit 1; }
mkdir -p "$STAGING" "$DEST"
trap 'rc=$?; echo "  FAILED line ${LINENO} (exit ${rc}): ${BASH_COMMAND}" >>"$LOG"' ERR
echo "=== $(date '+%F %T') backup of $REPO ===" >>"$LOG"

TS="$(date +%Y%m%d-%H%M%S)"
enc() { gpg --batch --yes --pinentry-mode loopback --symmetric --cipher-algo AES256 --passphrase-file "$PASS_FILE" -o "$2" "$1"; }

b="$STAGING/$PREFIX-repo-$TS.bundle"
git -C "$REPO" bundle create "$b" --all >>"$LOG" 2>&1
git -C "$REPO" bundle verify "$b" >/dev/null 2>&1
enc "$b" "$DEST/$PREFIX-repo-$TS.bundle.gpg"; rm -f "$b"

t="$STAGING/$PREFIX-files-$TS.tar.gz"
BIG="$STAGING/big-files-$TS.txt"
( cd "$REPO" && find . -path ./.git -prune -o -type f -size +"${BIG_FILE_LIMIT_MB}"M -print ) >"$BIG" 2>/dev/null || true
if [ -s "$BIG" ]; then
  echo "  excluded large files (>${BIG_FILE_LIMIT_MB} MB, NOT backed up):" >>"$LOG"
  sed 's/^/    /' "$BIG" >>"$LOG"
fi
EX=(); for e in $EXCLUDES; do EX+=(--exclude="$e"); done
tar -C "$REPO" --exclude-from="$BIG" "${MEDIA_EXCLUDES[@]}" ${EX[@]+"${EX[@]}"} --exclude='./.git' -czf "$t" . || {
  rc=$?; [ "$rc" -eq 1 ] || { echo "  tar FAILED (exit $rc)" >>"$LOG"; exit "$rc"; }
  echo "  tar warning: a file changed while being read (archive kept)" >>"$LOG"
}
enc "$t" "$DEST/$PREFIX-files-$TS.tar.gz.gpg"; rm -f "$t" "$BIG"

prune() {   # keep the newest $KEEP files matching a name pattern; log the count; zero is a warning
  local pat="$1" n=0 f
  local -a files=()
  while IFS= read -r -d '' f; do files+=("$f"); done < <(find "$DEST" -maxdepth 1 -type f -name "$pat" -print0)
  n=${#files[@]}
  echo "  prune $pat: found $n, keep $KEEP" >>"$LOG"
  if [ "$n" -eq 0 ]; then echo "  !! WARNING: ZERO matches for '$pat' in $DEST. The prune did nothing; investigate." >>"$LOG"; return 0; fi
  [ "$n" -gt "$KEEP" ] || return 0
  # names carry a sortable timestamp: newest first, drop the tail
  printf '%s\n' "${files[@]}" | sort -r | tail -n +"$((KEEP + 1))" | while IFS= read -r f; do
    # Prefer a recoverable delete (the sync client's trash) over rm where your platform offers one.
    rm -f -- "$f" && echo "  pruned $(basename "$f")" >>"$LOG"
  done
}
prune "$PREFIX-repo-*.bundle.gpg"
prune "$PREFIX-files-*.tar.gz.gpg"
echo "  OK $TS" >>"$LOG"
echo "backup complete: $TS (log: $LOG)"
