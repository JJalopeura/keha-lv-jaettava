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
sleep 6

CREDS="$(az webapp deployment list-publishing-credentials -g "$RG" -n "$APP" \
  --query "{u:publishingUserName,p:publishingPassword}" -o tsv)"
USER="$(echo "$CREDS" | cut -f1)"
PASS="$(echo "$CREDS" | cut -f2)"
# Kudu's VFS is rooted at /home, so /home/data is /api/vfs/data — not
# /api/vfs/home/data, which 404s and looks like "the file is not there".
KUDU="https://${APP}.scm.azurewebsites.net/api/vfs/data"

# Stop first. Deleting the SQLite file under a running worker is not safe: the VFS
# DELETE answers 409 while the app holds the file, and forcing it through Kudu's
# command API leaves a 4 KB database behind that has no tables — every request then
# fails with "no such table: customers" until the file is recreated from scratch.
echo "stopping ${APP}"
az webapp stop -g "$RG" -n "$APP" -o none
sleep 10

echo "  removing the database"
curl -s -u "$USER:$PASS" -H "Content-Type: application/json" -X POST \
  "https://${APP}.scm.azurewebsites.net/api/command" \
  -d '{"command":"rm -f /home/data/keha_lv.db /home/data/keha_lv.db-wal /home/data/keha_lv.db-shm","dir":"/home"}' \
  | grep -q '"ExitCode":0' || { echo "  rm failed" >&2; exit 1; }

code="$(curl -s -o /dev/null -w '%{http_code}' -u "$USER:$PASS" -X DELETE "$KUDU/uploads/?recursive=true")"
echo "  delete uploads/ -> $code"

echo "starting ${APP}"
az webapp start -g "$RG" -n "$APP" -o none

echo -n "waiting for ${HOST}/healthz "
for _ in $(seq 1 30); do
  if curl -fsS --max-time 10 "${HOST}/healthz" >/dev/null 2>&1; then
    echo "ok"
    curl -s --max-time 10 "${HOST}/healthz"; echo
    page="$(curl -s -o /dev/null -w '%{http_code}' -L --max-time 40 "$HOST/")"
    echo "login page: $page"
    if [ "$page" != "200" ]; then
      echo "the app is up but /login returned $page — the database did not come back clean" >&2
      exit 1
    fi
    echo "state after reset (still authenticated, before the policy is restored):"
    curl -s -u "$USER:$PASS" "$KUDU/" | python3 -c "import json,sys; [print('  ', i['name'], i.get('size', '')) for i in json.load(sys.stdin)]" 2>/dev/null || true
    exit 0
  fi
  echo -n "."
  sleep 10
done
echo
echo "health check did not pass; inspect: az webapp log tail -g ${RG} -n ${APP}" >&2
exit 1
