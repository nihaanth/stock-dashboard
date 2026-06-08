#!/usr/bin/env bash
# Long-running intra-day NSE announcement poll loop. Invoked by
# .github/workflows/live_poll.yml. Idempotent and self-terminating: it polls
# scripts/poll_live_news.py on the cadence from scripts/poll_schedule.py,
# commits + pushes the live data files when they change, and exits when the IST
# window closes or after LINK_DURATION_SEC.
#
# Env knobs (for local testing):
#   LINK_DURATION_SEC  max seconds before exiting          (default 18000 = 5h)
#   MAX_ITERS          stop after N iterations, 0=unlimited (default 0)
#   DRY_RUN            1 = poll but never commit/push       (default 0)
#   POLL_CMD           command run each iteration           (default python poller)
#   FORCE_PLAN         override cadence helper, e.g. "poll 1" (tests only)
set -uo pipefail   # no -e: a transient NSE/git error must never break the loop

ROOT="${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT"
PY="${PY:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || PY="python3"

LINK_DURATION_SEC="${LINK_DURATION_SEC:-18000}"
MAX_ITERS="${MAX_ITERS:-0}"
DRY_RUN="${DRY_RUN:-0}"
POLL_CMD="${POLL_CMD:-$PY scripts/poll_live_news.py}"

FILES=(frontdesign/data/_live_news.json
       frontdesign/data/_after_market_news.json
       frontdesign/data/_news_history.json)

if [ -n "${GITHUB_ACTIONS:-}" ]; then
  git config user.name  "stockbot-live[bot]"
  git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
fi

commit_and_push() {
  if git diff --quiet HEAD -- "${FILES[@]}"; then echo "  no diff; skip commit"; return 0; fi
  if [ "$DRY_RUN" = "1" ]; then echo "  DRY_RUN: would commit ${FILES[*]}"; return 0; fi
  git add "${FILES[@]}"
  git commit -m "live: announcements $(TZ=Asia/Kolkata date +%H:%M)" || return 0
  git pull --rebase --autostash origin main || true
  for i in 1 2 3; do
    if git push origin HEAD:main; then echo "  pushed (attempt $i)"; return 0; fi
    echo "  push failed; rebase + retry"
    git pull --rebase --autostash origin main || true
  done
  echo "  push failed after 3 attempts"
}

start=$(date +%s)
iters=0
while :; do
  if [ -n "${FORCE_PLAN:-}" ]; then
    read -r action cadence <<< "$FORCE_PLAN"
  else
    read -r action cadence < <("$PY" scripts/poll_schedule.py 2>/dev/null || echo "stop 0")
  fi

  if [ "$action" = "stop" ]; then
    echo "[loop] $(date -Iseconds) outside session window — exiting"; break
  fi

  echo "[loop] $(date -Iseconds) poll (cadence=${cadence}s)"
  $POLL_CMD
  rc=$?
  if [ "$rc" -eq 0 ]; then
    commit_and_push
  elif [ "$rc" -eq 1 ]; then
    echo "  poll: no new items"
  else
    echo "  poll: exit=$rc (fatal this cycle)"
  fi

  iters=$((iters + 1))
  if [ "$MAX_ITERS" -gt 0 ] && [ "$iters" -ge "$MAX_ITERS" ]; then
    echo "[loop] reached MAX_ITERS=$MAX_ITERS — exiting"; break
  fi
  if [ $(( $(date +%s) - start )) -ge "$LINK_DURATION_SEC" ]; then
    echo "[loop] reached LINK_DURATION_SEC — exiting"; break
  fi
  sleep "$cadence"
done
