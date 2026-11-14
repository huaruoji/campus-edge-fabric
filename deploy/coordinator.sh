#!/usr/bin/env bash
# One command to start the control plane.
#   ./deploy/coordinator.sh [--host 0.0.0.0] [--port 8080]
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
if [ ! -x .venv/bin/cef-coord ]; then
  echo "installing coordinator role ..."
  uv sync --package cef-coordinator --quiet
fi

exec .venv/bin/cef-coord serve "$@"
