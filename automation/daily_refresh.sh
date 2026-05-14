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

  echo "[1/6] downloading today's MTO + bhavcopy..."
  "$PY" scripts/download_today.py || echo "  download_today.py exited non-zero (likely non-trading day)"

  echo
  echo "[2/6] refreshing 2026.webarchive (last 7d)..."
  "$PY" scripts/refresh_webarchive_today.py 7 || echo "  webarchive refresh failed; continuing"

  echo
  echo "[3/6] building dashboard JSON (post-mortem: prev-day predictions vs today)..."
  "$PY" scripts/build_dashboard_data.py

  echo
  echo "[4/6] building today's forecast (predictions for next session)..."
  "$PY" scripts/build_dashboard_data.py --forecast || echo "  forecast build failed; continuing"

  echo
  echo "[5/6] rebuilding industries tree (90d price + delivery + news)..."
  "$PY" scripts/build_industry_folders.py || echo "  industries rebuild failed; continuing"

  echo
  echo "[6/6] committing + pushing dashboard data..."
  if [ ! -d .git ]; then
    echo "  no .git directory — run 'git init && git remote add origin <url>' once. Skipping push."
    exit 0
  fi
  git add frontdesign/data/predictions_*.json \
          frontdesign/data/latest.json \
          frontdesign/data/forecast.json \
          frontdesign/data/index.json \
          frontdesign/data/industries \
          frontdesign/d
  if git diff --cached --quiet; then
    echo "  no dashboard changes to commit"
  else
    git commit -m "dashboard: daily refresh $(date +%F)"
    if git remote get-url origin >/dev/null 2>&1; then
      # Rebase on anything the live_poll bot pushed during the trading session,
      # then push with retries. Without this, daily refreshes silently fail to
      # push because live_poll commits _live_news.json every 5 min.
      pushed=0
      for i in 1 2 3; do
        git pull --rebase --autostash origin main || echo "  pull --rebase attempt $i failed"
        if git push origin HEAD:main; then
          echo "  pushed on attempt $i"
          pushed=1
          break
        fi
        echo "  push failed on attempt $i; retrying after rebase"
      done
      [ "$pushed" -eq 1 ] || echo "  git push failed after 3 attempts — check credentials / branch"
    else
      echo "  no 'origin' remote set; commit kept locally"
    fi
  fi

  echo "[done] $(date -Iseconds)"
} >> "$LOG" 2>&1
