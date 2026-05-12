#!/usr/bin/env bash
# Intra-day NSE announcement poll: poll → write _live_news.json → push if changed.
# Triggered by automation/com.stockbot.live.plist every 2 minutes during market hours.
# Manual run:  bash automation/live_refresh.sh
set -uo pipefail   # no -e on purpose: never let a transient NSE error break the loop

ROOT=/Users/nihaanthreddy/devl/Learning/Projects/stockbot
cd "$ROOT"

LOG="$ROOT/automation/live_refresh.log"
PY="$ROOT/.venv/bin/python"

{
  echo "--- live_refresh @ $(date -Iseconds) ---"

  "$PY" scripts/poll_live_news.py
  rc=$?

  if [ $rc -eq 0 ]; then
    git add frontdesign/data/industries/_live_news.json
    if git diff --cached --quiet; then
      echo "  no diff after stage (unexpected); skipping commit"
    else
      git commit -m "live: announcements $(date +%H:%M)"
      if git remote get-url origin >/dev/null 2>&1; then
        git push origin HEAD || echo "  push failed; will retry next cycle"
      else
        echo "  no origin remote; commit kept locally"
      fi
    fi
  elif [ $rc -eq 1 ]; then
    echo "  poll: no new items, skipping commit"
  else
    echo "  poll: exit=$rc (fatal); skipping commit"
  fi
} >> "$LOG" 2>&1
