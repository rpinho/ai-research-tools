#!/bin/sh
# Local daily ingestion and scoring; no git operations, networking or publication.
set -eu
exec python3 "$(dirname "$0")/model-roles.py" daily "$@"
