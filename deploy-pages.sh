#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

python3 build-costs.py
cp dist/index.html ./index.html

git add build-costs.py deploy-pages.sh dist/index.html logs/costs.jsonl index.html

if git diff --cached --quiet; then
  echo "No changes to commit."
  exit 0
fi

git commit -m "Update cost dashboard"
git push
