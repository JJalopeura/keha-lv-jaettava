#!/usr/bin/env bash
# Deploy a previously released artifact from deploy/artifacts/ and verify it serves.
#
#   ./scripts/rollback.sh                 # list what is available
#   ./scripts/rollback.sh 75000d5         # roll back to that commit's artifact
#   ./scripts/rollback.sh path/to/app.zip # deploy any zip you trust
#
# The artifact is the backup: it was built by scripts/build_deploy_zip.sh from a
# specific commit and committed with it, so a rollback needs no rebuild and no
# branch state. State lives in /home/data and is unaffected by a deploy.
set -euo pipefail

cd "$(dirname "$0")/.."

RG="rg-hackathon-ecdx-proto-sdc-001"
APP="keha-lv-web"
ARTIFACT_DIR="deploy/artifacts"

usage() {
  echo "available artifacts:"
  ls -1 "$ARTIFACT_DIR"/*.zip 2>/dev/null | sed 's|^|  |' || echo "  (none — run ./scripts/build_deploy_zip.sh)"
}

if [ "$#" -lt 1 ]; then
  usage
  exit 0
fi

TARGET="$1"
if [ -f "$TARGET" ]; then
  ZIP="$TARGET"
else
  ZIP="${ARTIFACT_DIR}/keha-lv-${TARGET}.zip"
fi

if [ ! -f "$ZIP" ]; then
  echo "error: no artifact at ${ZIP}" >&2
  usage >&2
  exit 1
fi

echo "deploying ${ZIP} to ${APP} (${RG})"
az webapp deploy -g "$RG" -n "$APP" --src-path "$ZIP" --type zip >/dev/null

HOST="https://${APP}.azurewebsites.net"
echo -n "waiting for ${HOST}/healthz "
for _ in $(seq 1 30); do
  if curl -fsS --max-time 10 "${HOST}/healthz" >/dev/null 2>&1; then
    echo "ok"
    curl -s --max-time 10 "${HOST}/healthz"; echo
    exit 0
  fi
  echo -n "."
  sleep 10
done

echo
echo "health check did not pass; inspect: az webapp log tail -g ${RG} -n ${APP}" >&2
exit 1
