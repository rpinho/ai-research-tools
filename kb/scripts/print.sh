#!/usr/bin/env bash
# print.sh: THE way to print a hand-over document from the KB. Lint, then render, then print.
#
# Why: `lp` has no opinion about content. A sheet printed with a bare `lp` once carried a false
# "nothing outstanding" into a room where it mattered. This wrapper runs scripts/prep-lint.py
# first; a conflict with state.md stops the job. Override only with PREP_LINT_SKIP=1 (it names
# itself in the output).
#
# usage: scripts/print.sh FILE [FILE...]     (.html is rendered to PDF with headless Chrome)
# env:   KB_PRINTER (lp destination; default = system default printer)
#        KB_PRINT_PAGES (e.g. 1 to reprint only page 1)
#        CHROME (path to a Chrome/Chromium binary for HTML -> PDF)
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
DEST=(); [ -n "${KB_PRINTER:-}" ] && DEST=(-d "$KB_PRINTER")
PAGES=(); [ -n "${KB_PRINT_PAGES:-}" ] && PAGES=(-P "$KB_PRINT_PAGES")
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
tmp="${TMPDIR:-/tmp}/kb-print-$$"; mkdir -p "$tmp"

[ $# -ge 1 ] || { echo "usage: $0 FILE [FILE...]" >&2; exit 2; }

if [ "${PREP_LINT_SKIP:-}" = "1" ]; then
  echo "print.sh: PREP_LINT_SKIP=1, content lint bypassed for: $*" >&2
else
  python3 "$here/prep-lint.py" "$@" || { echo "print.sh: REFUSED. Fix the document or mark verified claims." >&2; exit 1; }
fi

for f in "$@"; do
  case "${f##*.}" in
    html|htm)
      out="$tmp/$(basename "${f%.*}").pdf"
      "$CHROME" --headless=new --disable-gpu --no-pdf-header-footer --print-to-pdf="$out" \
        "file://$(cd "$(dirname "$f")" && pwd)/$(basename "$f")" 2>/dev/null
      lp ${DEST[@]+"${DEST[@]}"} ${PAGES[@]+"${PAGES[@]}"} -o fit-to-page=false -o scaling=100 "$out" ;;
    pdf|jpg|jpeg|png|txt|md)
      lp ${DEST[@]+"${DEST[@]}"} ${PAGES[@]+"${PAGES[@]}"} "$f" ;;
    *) echo "print.sh: unsupported type: $f" >&2; exit 2 ;;
  esac
done
