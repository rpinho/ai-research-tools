#!/bin/sh
# Compatibility name: generate a local Markdown export for review. Never publishes.
set -eu
exec python3 "$(dirname "$0")/model-roles.py" public "$@"
