#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  set -a
  source "$SCRIPT_DIR/.env"
  set +a
fi

export KEHA_LV_LLM_PROVIDER="${KEHA_LV_LLM_PROVIDER:-mock}"
export KEHA_LV_DEMO_MODE="${KEHA_LV_DEMO_MODE:-true}"
export KEHA_LV_FORCE_MOCK_LLM="${KEHA_LV_FORCE_MOCK_LLM:-true}"
PYTHON="${PYTHON:-$SCRIPT_DIR/.venv/bin/python}"
[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3 || command -v python)"
exec "$PYTHON" -m uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}"
