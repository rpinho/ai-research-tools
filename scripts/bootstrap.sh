#!/bin/sh
# Reuse the repository venv; never read shell profiles or provider credentials.
set -eu
REPO=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$REPO"
PYTHON=${PYTHON:-python3}
"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 9), "Python 3.9 or newer is required"'
if [ ! -x .venv/bin/python ]; then
    "$PYTHON" -m venv .venv
fi
.venv/bin/python -m pip --disable-pip-version-check install -r requirements-dev.txt
cat > .venv/bin/rh <<'ENTRY'
#!/bin/sh
REPO=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
exec "$REPO/.venv/bin/python" "$REPO/harness/rh.py" "$@"
ENTRY
chmod +x .venv/bin/rh
.venv/bin/python harness/rh.py --help >/dev/null
.venv/bin/python -c 'import numpy; import sys; sys.path.insert(0, "selection"); import selection_gate; print("Bootstrap smoke test: PASS")'
printf '%s\n' 'Next: . .venv/bin/activate && python3 -m pytest -q' 'Then: scripts/install-harness.sh (tests and isolated offline toy preview)'
