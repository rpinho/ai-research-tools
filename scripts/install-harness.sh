#!/bin/sh
set -eu
REPO=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$REPO"
scripts/bootstrap.sh
.venv/bin/python -m pytest -q
DEMO_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/research-toy.XXXXXX")
.venv/bin/python examples/toy_demo.py --root "$DEMO_ROOT"
