#!/usr/bin/env bash
# Daily refresh: download today's NSE data, build dashboard JSON, commit + push.
# Triggered by automation/com.stockbot.dashboard.plist at 19:30 IST.
# Manual run for debugging:  bash automation/daily_refresh.sh
set -euo pipefail

ROOT=/Users/nihaanthreddy/devl/Learning/Projects/stockbot
cd "$ROOT"

LOG="$ROOT/automation/daily_refresh.log"
PY="$ROOT/.venv/bin/python"

# Append a divider so successive days are easy to scan
{
  echo
  echo "================================================================"
  echo "  daily_refresh @ $(date -Iseconds)"
  echo "================================================================"

  echo "[1/5] downloading today's MTO + bhavcopy..."
  "$PY" scripts/download_today.py || echo "  download_today.py exited non-zero (likely non-trading day)"

  echo
  echo "[2/5] refreshing 2026.webarchive (last 7d)..."
  "$PY" scripts/refresh_webarchive_today.py 7 || echo "  webarchive refresh failed; continuing"

  echo
  echo "[3/5] building dashboard JSON..."
  "$PY" scripts/build_dashboard_data.py

  echo
  echo "[4/5] rebuilding industries tree (90d price + delivery + news)..."
  "$PY" scripts/build_industry_folders.py || echo "  industries rebuild failed; continuing"

  echo
  echo "[5/5] committing + pushing dashboard data..."
  if [ ! -d .git ]; then
    echo "  no .git directory — run 'git init && git remote add origin <url>' once. Skipping push."
    exit 0
  fi
  git add frontdesign/data/predictions_*.json \
          frontdesign/data/latest.json \
          frontdesign/data/index.json \
          frontdesign/data/industries
  if git diff --cached --quiet; then
    echo "  no dashboard changes to commit"
  else
    git commit -m "dashboard: daily refresh $(date +%F)"
    if git remote get-url origin >/dev/null 2>&1; then
      git push origin HEAD || echo "  git push failed — check credentials / branch"
    else
      echo "  no 'origin' remote set; commit kept locally"
    fi
  fi

  echo "[done] $(date -Iseconds)"
} >> "$LOG" 2>&1
