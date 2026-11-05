#!/bin/bash
# Install this alongside the declared setup script on a Linux pod.
# Usage: pod/setup.sh /workspace/setup.sh [args...]
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
# Do not use --foreground: timeout must signal the complete setup process group.
exec setsid timeout --kill-after=30s "${RH_SETUP_TIMEOUT:-15m}" bash -euo pipefail "$@"
