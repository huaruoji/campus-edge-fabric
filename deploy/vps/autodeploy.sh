#!/usr/bin/env bash
# Pull origin/main, re-sync deps, restart the coordinator. Runs from a systemd timer
# every 60 s, so pushing to main *is* the deploy. The built dashboard is committed
# under packages/cef-coordinator/src/cef_coordinator/web, so the VPS needs no node.
set -euo pipefail
export PATH=/root/.local/bin:/usr/local/bin:$PATH
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
/root/.local/bin/uv sync --all-packages --frozen --python 3.12 --quiet
systemctl restart cef-coord
for _ in $(seq 1 20); do
  curl -sf http://127.0.0.1:8090/api/v1/health >/dev/null && { echo "deploy ok, health 200"; exit 0; }
  sleep 0.5
done
echo "deploy FAILED: health check timed out" >&2
exit 1
