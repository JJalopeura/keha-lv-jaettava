#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  set -a
  source "$SCRIPT_DIR/.env"
  set +a
fi

PYTHON="${PYTHON:-$SCRIPT_DIR/.venv/bin/python}"
[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3 || command -v python)"

# Robust TLS on macOS / uv-managed Pythons: pin a stable CA bundle so HTTPS calls
# keep working even if the venv's certifi bundle is missing or gets wiped while
# the server is running (httpx honors SSL_CERT_FILE via trust_env).
if [[ -z "${SSL_CERT_FILE:-}" ]]; then
  CA=""
  if [[ -x "$PYTHON" ]] && "$PYTHON" -c 'import certifi' 2>/dev/null; then
    CA="$("$PYTHON" -c 'import certifi; print(certifi.where())' 2>/dev/null || true)"
  fi
  [[ -n "$CA" && -f "$CA" ]] || CA="/private/etc/ssl/cert.pem"
  if [[ -f "$CA" ]]; then
    export SSL_CERT_FILE="$CA"
  fi
fi

exec "$PYTHON" -m uvicorn app.main:app --host "${APP_HOST:-127.0.0.1}" --port "${APP_PORT:-8002}"
