#!/bin/sh
set -eu
REPO=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ "$#" -lt 3 ] || [ "$1" != --root ] || [ "$#" -gt 4 ]; then
    printf '%s\n' 'Usage: scripts/run-sweep.sh --root ROOT NAME [--execute]' >&2
    exit 2
fi
LANE_ROOT=$2
LANE_NAME=$3
if [ "$#" -eq 4 ] && [ "$4" != --execute ]; then
    printf '%s\n' 'Expected --execute' >&2
    exit 2
fi
"$REPO/.venv/bin/python" "$REPO/harness/rh.py" --root "$LANE_ROOT" sweep "$LANE_NAME" --dry-run
if [ "$#" -eq 4 ]; then
    exec "$REPO/.venv/bin/python" "$REPO/harness/rh.py" --root "$LANE_ROOT" sweep "$LANE_NAME"
fi
