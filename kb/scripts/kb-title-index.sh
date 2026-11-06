#!/usr/bin/env bash
# Emit a COMPACT title index of the wiki: every page title grouped by domain, no descriptions.
# Used by the SessionStart hook (.claude/hooks/inject-title-index.sh); also handy by hand.
#
# Pages that follow a bulk naming pattern (e.g. hundreds of "<name> - Profile.md") collapse to
# one comma-separated line: listing them individually is bulk with no lookup value.
# Set KB_TITLE_COLLAPSE to the suffix you want collapsed (default " - Profile").
set -uo pipefail
cd "${KB_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || echo .)}" || exit 0
[ -d wiki ] || exit 0
SUFFIX="${KB_TITLE_COLLAPSE:- - Profile}"

total=$(find wiki -name '*.md' | wc -l | tr -d ' ')
printf 'KB TITLE INDEX (auto-generated, %s pages). Full descriptions: index.md\n' "$total"
printf 'Before answering "do we have X?" or writing "we never documented X":\n'
printf '   grep this list, then grep -ni "<term>" index.md, and NEVER conclude absence\n'
printf '   from output piped through head or -m. If the answer is no, prove it untruncated.\n\n'

find wiki -mindepth 1 -maxdepth 1 -type d | sed 's|^wiki/||' | sort | while IFS= read -r d; do
  files=$(find "wiki/$d" -name '*.md' ! -name "*$SUFFIX.md" 2>/dev/null | sed "s|^wiki/$d/||;s|\.md$||" | sort)
  n=$(printf '%s\n' "$files" | grep -c . || true)
  [ "$n" -eq 0 ] && continue
  printf '## %s/ (%s)\n' "$d" "$n"
  printf '%s\n' "$files" | sed 's/^/  /'
  printf '\n'
done

collapsed=$(find wiki -name "*$SUFFIX.md" 2>/dev/null | sed "s|.*/||;s|$SUFFIX\.md$||" | sort)
nc=$(printf '%s\n' "$collapsed" | grep -c . || true)
if [ "$nc" -gt 0 ]; then
  printf '## "*%s" pages (%s), pattern "<name>%s.md"\n' "$SUFFIX" "$nc" "$SUFFIX"
  printf '%s\n' "$collapsed" | paste -sd, - | fold -s -w 110 | sed 's/^/  /'
fi
