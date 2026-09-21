#!/usr/bin/env bash
# ============================================================
#  ONE-COMMAND PUSH
#  Refresh the DB clone, then stage EVERYTHING (code + DB clone
#  + .env + requirements) and commit + push in one go.
#  Usage:  bash dbpush.sh "optional commit message"
# ============================================================
set -uo pipefail
cd "$(cd "$(dirname "$0")" && pwd)"

echo "== 1/4 refreshing DB clone =="
bash db/clone_db.sh || { echo "DB clone failed -- push aborted"; exit 1; }

echo "== 2/4 staging =="
git add -A

echo "== 3/4 commit =="
git commit -m "${1:-update: code + DB clone $(date '+%F %H:%M')}" || echo "(nothing to commit)"

echo "== 4/4 push =="
git push
echo "== done =="
