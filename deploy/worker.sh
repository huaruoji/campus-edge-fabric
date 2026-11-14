#!/usr/bin/env bash
# One command to join a device to the pool.
#   ./deploy/worker.sh --server https://cef.huaruoji.com --token <TOKEN> [--name pc --slots 2]
set -euo pipefail
cd "$(dirname "$0")/.."

# install only the worker role + the workload deps people actually need on a device
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
if [ ! -x .venv/bin/cef-worker ]; then
  echo "installing worker role (this includes the cef CLI) ..."
  uv sync --package cef-worker --extra all --quiet || uv sync --package cef-worker --quiet
fi

exec .venv/bin/cef-worker run "$@"
