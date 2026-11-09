#!/usr/bin/env bash
# Build the deployable zip for App Service from a git commit, and keep it in the repo.
#
#   ./scripts/build_deploy_zip.sh            # artifact for HEAD -> deploy/artifacts/
#   ./scripts/build_deploy_zip.sh <rev>      # artifact for any commit (rollback rebuild)
#
# Why `git archive` and not `zip -r .`: the artifact must contain exactly what was
# released, no untracked files, no __pycache__, and file timestamps taken from the
# commit so two runs of the same commit produce the same bytes. That is what makes
# deploy/artifacts/*.zip a usable backup: rolling back is deploying a file, not
# rebuilding from a branch that may have moved.
set -euo pipefail

cd "$(dirname "$0")/.."

REV="${1:-HEAD}"
SHA="$(git rev-parse --short=12 "$REV")"
OUT_DIR="deploy/artifacts"
OUT="${OUT_DIR}/keha-lv-${SHA}.zip"

mkdir -p "$OUT_DIR"

if [ "${REV}" = "HEAD" ] && [ -n "$(git status --porcelain)" ]; then
  echo "warning: working tree is dirty; the artifact is built from HEAD (${SHA}), not from your edits" >&2
fi

# Tracked paths only: the app package, its dependency manifest and the gunicorn config.
git archive --format=zip --prefix="" -o "$OUT" "$REV" \
  app requirements.txt gunicorn.conf.py

BYTES="$(wc -c < "$OUT" | tr -d ' ')"
echo "artifact: ${OUT} (${BYTES} bytes, commit ${SHA})"
echo "rollback: ./scripts/rollback.sh ${SHA}"
