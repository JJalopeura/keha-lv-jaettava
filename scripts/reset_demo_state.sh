#!/usr/bin/env bash
# Wipe the demo database and uploads in /home/data so the next demo starts clean.
#
#   ./scripts/reset_demo_state.sh
#
# App Service keeps /home across deploys and idle unloads, which is what makes the
# demo durable — and also what makes stale test cases accumulate. This deletes
# /home/data/keha_lv.db (plus WAL/SHM) and /home/data/uploads, then restarts the app
# so it recreates the schema and the three-account roster.
#
# Access: Kudu (SCM) basic auth is disabled by policy on this app, so the script
# flips `basicPublishingCredentialsPolicies/scm` on for the duration and turns it off
# again on exit, including on failure.
set -euo pipefail

RG="${RG:-rg-hackathon-ecdx-proto-sdc-001}"
APP="${APP:-keha-lv-web}"
HOST="https://${APP}.azurewebsites.net"
SUB="$(az account show --query id -o tsv)"
POLICY="https://management.azure.com/subscriptions/${SUB}/resourceGroups/${RG}/providers/Microsoft.Web/sites/${APP}/basicPublishingCredentialsPolicies/scm?api-version=2022-03-01"

set_scm_auth() {
  az rest --method put --url "$POLICY" \
    --headers "Content-Type=application/json" \
    --body "{\"properties\":{\"allow\":$1}}" -o none
}

cleanup() {
  set_scm_auth false || echo "warning: could not disable SCM basic auth again" >&2
}
trap cleanup EXIT

echo "enabling SCM basic auth for the reset"
set_scm_auth true
sleep 5

CREDS="$(az webapp deployment list-publishing-credentials -g "$RG" -n "$APP" \
  --query "{u:publishingUserName,p:publishingPassword}" -o tsv)"
USER="$(echo "$CREDS" | cut -f1)"
PASS="$(echo "$CREDS" | cut -f2)"
KUDU="https://${APP}.scm.azurewebsites.net/api/vfs/home/data"

for f in keha_lv.db keha_lv.db-wal keha_lv.db-shm; do
  code="$(curl -s -o /dev/null -w '%{http_code}' -u "$USER:$PASS" -X DELETE "$KUDU/$f")"
  echo "  delete $f -> $code"
done

code="$(curl -s -o /dev/null -w '%{http_code}' -u "$USER:$PASS" -X DELETE "$KUDU/uploads/?recursive=true")"
echo "  delete uploads/ -> $code"

echo "restarting ${APP}"
az webapp restart -g "$RG" -n "$APP" -o none

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
