#!/usr/bin/env bash
# Pull origin/main, rebuild whatever changed, restart the coordinator.
# Runs from a systemd timer every 60 s: pushing to main *is* the deploy.
#
# Two deployables:
#   backend   -> packages/ (uv sync, venv entrypoint)
#   dashboard -> web/ (vite build straight into the coordinator package)
# The committed build output is a fallback: if npm fails, we keep serving it.
set -euo pipefail
export PATH=/opt/node/bin:/root/.local/bin:/usr/local/bin:$PATH
export UV_PYTHON_INSTALL_DIR=/opt/pythons
REPO=/opt/campus-edge-fabric
cd "$REPO"
before=$(git rev-parse HEAD)
GIT_SSH_COMMAND='ssh -i /root/.ssh/cef_deploy -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new' \
  git fetch -q origin main
after=$(git rev-parse origin/main)
if [ "$before" = "$after" ]; then
  exit 0
fi
echo "deploy: $before -> $after"
git reset --hard -q "$after"

# --- backend -------------------------------------------------------------
/root/.local/bin/uv sync --all-packages --frozen --python 3.12 --quiet

# --- dashboard -----------------------------------------------------------
if [ -f web/package.json ]; then
  if [ ! -f web/node_modules/.package-lock.json ] || [ web/package-lock.json -nt web/node_modules/.package-lock.json ]; then
    echo "npm ci (deps changed)"
    (cd web && npm ci --no-audit --no-fund --silent) || echo "WARN: npm ci failed, keeping previous build"
  fi
  if (cd web && npm run build); then
    echo "frontend built: $(ls packages/cef-coordinator/src/cef_coordinator/web/assets | tr '\n' ' ')"
  else
    echo "WARN: frontend build failed, serving committed dist" >&2
  fi
fi

systemctl restart cef-coord
for _ in $(seq 1 20); do
  curl -sf http://127.0.0.1:8090/api/v1/health >/dev/null && { echo "deploy ok, health 200"; exit 0; }
  sleep 0.5
done
echo "deploy FAILED: health check timed out" >&2
exit 1
