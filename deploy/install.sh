#!/usr/bin/env bash
# One install command, four roles. Installs only what that role needs.
#
#   ./deploy/install.sh --role coordinator          # FastAPI + scheduler + dashboard
#   ./deploy/install.sh --role worker               # agent + thumbnail (CPU image work)
#   ./deploy/install.sh --role worker --extra infer # + onnxruntime (object detection)
#   ./deploy/install.sh --role worker --extra all   # + onnxruntime/numpy
#   ./deploy/install.sh --role client               # cef CLI + MCP server
#   ./deploy/install.sh --role all                  # everything (dev machine)
set -euo pipefail
cd "$(dirname "$0")/.."

ROLE="all"
EXTRA=""
while [ $# -gt 0 ]; do
  case "$1" in
    --role) ROLE="$2"; shift 2 ;;
    --extra) EXTRA="$2"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if ! command -v uv >/dev/null 2>&1; then
  echo "installing uv ..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

EXTRA_FLAG=""
if [ -n "$EXTRA" ]; then
  case "$EXTRA" in
    infer|thumbnail|blender|all) EXTRA_FLAG="--extra $EXTRA" ;;
    *) echo "unknown extra: $EXTRA (thumbnail|infer|blender|all)" >&2; exit 2 ;;
  esac
fi

case "$ROLE" in
  coordinator) uv sync --package cef-coordinator --package cef-client $EXTRA_FLAG --quiet ;;
  worker)      uv sync --package cef-worker --package cef-client $EXTRA_FLAG --quiet ;;
  client)      uv sync --package cef-client $EXTRA_FLAG --quiet ;;
  all)         uv sync --all-packages --all-extras --quiet ;;
  *) echo "unknown role: $ROLE (coordinator|worker|client|all)" >&2; exit 2 ;;
esac

echo "installed role: $ROLE ${EXTRA:+($EXTRA)}"
case "$ROLE" in
  coordinator) echo "start it with: ./.venv/bin/cef-coord serve --host 0.0.0.0 --port 8080" ;;
  worker)      echo "start it with: ./.venv/bin/cef-worker run --server <url> --token <token>" ;;
  client)      echo "try:          ./.venv/bin/cef doctor" ;;
  all)         echo "try:          ./.venv/bin/cef-coord serve --port 8080" ;;
esac
