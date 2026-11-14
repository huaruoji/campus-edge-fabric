#!/usr/bin/env bash
# One command to join a device to the pool.
#   ./deploy/worker.sh --server http://<coordinator>:8080 --token <TOKEN> [--name pc --slots 2 --workloads thumbnail]
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
  echo "installing uv ..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

EXTRA=""
for arg in "$@"; do
  case "$arg" in
    *infer*) EXTRA="--extra infer" ;;
    *blender*) ;;
  esac
done
# install the optional workload deps up front so the device advertises capabilities it really has
uv sync --all-packages --extra infer >/dev/null 2>&1 || uv sync --all-packages >/dev/null
exec uv run cef-worker run --workloads thumbnail,infer,blender "$@"
