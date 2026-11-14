#!/usr/bin/env bash
# One command to start the control plane. Installs uv if missing, syncs deps, serves.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
  echo "installing uv ..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

uv sync --all-packages >/dev/null
exec uv run cef-coord serve "$@"
