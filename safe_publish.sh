#!/usr/bin/env bash
set -euo pipefail

SRC_ROOT="/Users/sasi/Desktop/crypto_bot"
DST_ROOT="/Users/sasi/Documents/Playground"

COMMIT_MSG="${1:-chore: sync publish-safe files}"

copy_file() {
  local src="$1"
  local dst="$2"
  mkdir -p "$(dirname "$dst")"
  cp "$src" "$dst"
}

echo "[1/8] Verify GitHub CLI"
gh --version >/dev/null
gh auth status >/dev/null

echo "[2/8] Validate Python files"
PYTHONPYCACHEPREFIX=/tmp/crypto_bot_pycache python3 -m py_compile \
  "$SRC_ROOT/api_server.py" \
  "$SRC_ROOT/recheck.py"

echo "[3/8] Copy publish-safe files"
copy_file "$SRC_ROOT/api_server.py" "$DST_ROOT/api_server.py"
copy_file "$SRC_ROOT/recheck.py" "$DST_ROOT/recheck.py"
copy_file "$SRC_ROOT/dashboard.html" "$DST_ROOT/dashboard.html"
copy_file "$SRC_ROOT/docker-compose.yml" "$DST_ROOT/docker-compose.yml"
copy_file "$SRC_ROOT/Dockerfile" "$DST_ROOT/Dockerfile"
copy_file "$SRC_ROOT/export_log.py" "$DST_ROOT/export_log.py"
copy_file "$SRC_ROOT/clean_signal_errors.py" "$DST_ROOT/clean_signal_errors.py"
copy_file "$SRC_ROOT/bot_set2.py" "$DST_ROOT/bot_set2.py"
copy_file "$SRC_ROOT/bot_set3.py" "$DST_ROOT/bot_set3.py"
copy_file "$SRC_ROOT/config_set2.py" "$DST_ROOT/config_set2.py"
copy_file "$SRC_ROOT/bot_set3.py" "$DST_ROOT/bot.py"
copy_file "$SRC_ROOT/config_set2.py" "$DST_ROOT/config.py"
copy_file "$SRC_ROOT/SET3v1_INDICATOR_CHANGES.md" "$DST_ROOT/SET3v1_INDICATOR_CHANGES.md"
copy_file "$SRC_ROOT/PRODUCTION_BUG_REPORT_2026-06-13.md" "$DST_ROOT/PRODUCTION_BUG_REPORT_2026-06-13.md"
copy_file "$SRC_ROOT/README.md" "$DST_ROOT/README.md"
copy_file "$SRC_ROOT/safe_publish.sh" "$DST_ROOT/safe_publish.sh"
copy_file "$SRC_ROOT/.github/workflows/secret-guard.yml" "$DST_ROOT/.github/workflows/secret-guard.yml"

chmod +x "$DST_ROOT/safe_publish.sh"

echo "[4/8] Secret guard scan"
if rg -n \
  -e 'ghp_[A-Za-z0-9]{20,}' \
  -e 'github_pat_[A-Za-z0-9_]{20,}' \
  -e 'sk-ant-api[0-9A-Za-z_-]+' \
  -e 'AKIA[0-9A-Z]{16}' \
  -e 'AIza[0-9A-Za-z\\-_]{20,}' \
  -e 'xox[baprs]-[A-Za-z0-9-]+' \
  -e '-----BEGIN (RSA|OPENSSH|EC|DSA) PRIVATE KEY-----' \
  -e 'TELEGRAM_TOKEN=[0-9]{6,}:[A-Za-z0-9_-]{20,}' \
  -e 'TG_BOT_TOKEN=[0-9]{6,}:[A-Za-z0-9_-]{20,}' \
  -e 'ANTHROPIC_API_KEY=sk-ant-api[0-9A-Za-z_-]+' \
  "$DST_ROOT"
then
  echo "Secret guard failed: possible real secret detected."
  exit 1
fi

echo "[5/8] Forbidden path guard"
if git -C "$DST_ROOT" ls-files | rg -n '(^|/)(\\.env|\\.htpasswd|data/|export/|env/)' ; then
  echo "Forbidden tracked path detected."
  exit 1
fi

echo "[6/8] Stage explicit files"
git -C "$DST_ROOT" add \
  api_server.py \
  recheck.py \
  dashboard.html \
  docker-compose.yml \
  Dockerfile \
  export_log.py \
  clean_signal_errors.py \
  bot_set2.py \
  bot_set3.py \
  config_set2.py \
  bot.py \
  config.py \
  SET3v1_INDICATOR_CHANGES.md \
  PRODUCTION_BUG_REPORT_2026-06-13.md \
  README.md \
  safe_publish.sh \
  .github/workflows/secret-guard.yml

echo "[7/8] Review staged diff"
git -C "$DST_ROOT" diff --cached --name-only

if git -C "$DST_ROOT" diff --cached --quiet; then
  echo "No publish-safe changes to commit."
  exit 0
fi

echo "[8/8] Commit and push"
git -C "$DST_ROOT" commit -m "$COMMIT_MSG"
git -C "$DST_ROOT" push origin main

echo "Done."
