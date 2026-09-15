#!/usr/bin/env bash
# Long-running intra-day NSE announcement poll loop. Invoked by
# .github/workflows/live_poll.yml. Idempotent and self-terminating: it polls
# scripts/poll_live_news.py on the cadence from scripts/poll_schedule.py,
# commits + pushes the live data files when they change, and exits when the IST
# window closes or after LINK_DURATION_SEC.
#
# Git discipline (this is what protects the append-only archive):
#   * Before EVERY poll the tree is reset to the current tip of main, so the
#     poller merges into the newest archive -- never into the checkout the job
#     started with, which can be hours old after a nap or a backfill push.
#   * Commits are pushed as plain fast-forwards. If main moved in between, the
#     push is rejected; we reset to main again and re-run the poll (it is
#     idempotent: dedup by seq_id) instead of rebasing. A conflicted rebase that
#     was silently ignored is exactly how _news_history.json got wiped seven
#     times in 2026 (see docs/news-archive-wipes.md).
#   * scripts/guard_news_files.py runs before every commit and refuses an
#     archive that does not parse or that is smaller than the one in HEAD.
#
# Env knobs (for local testing):
#   LINK_DURATION_SEC  max seconds before exiting          (default 18000 = 5h)
#   MAX_ITERS          stop after N iterations, 0=unlimited (default 0)
#   DRY_RUN            1 = poll but never commit/push       (default 0)
#   POLL_CMD           command run each iteration           (default python poller)
#   FORCE_PLAN         override cadence helper, e.g. "poll 1" (tests only)
#   SLEEP_TO_NEXT_WINDOW  1 = outside the window, take ONE capped nap toward the
#                      next window open instead of exiting (chain relay; the
#                      workflow sets this — local/manual runs keep exit-on-stop)
#   SLEEP_CAP_SEC      max single nap (default 16200 = 4.5h, under the 6h job cap)
#   MIN_SLEEP_SEC      nap floor so a broken helper can't cause a dispatch storm
#   NO_SYNC            1 = never fetch/reset the tree from origin/main. Defaults
#                      to 0 under GitHub Actions and 1 elsewhere, so a manual run
#                      on a dev machine never hard-resets a working tree.
#   GUARD              path to the pre-commit guard (default scripts/guard_news_files.py)
set -uo pipefail   # no -e: a transient NSE/git error must never break the loop

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${ROOT:-$(cd "$HERE/.." && pwd)}"
cd "$ROOT"
PY="${PY:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || PY="python3"

LINK_DURATION_SEC="${LINK_DURATION_SEC:-18000}"
MAX_ITERS="${MAX_ITERS:-0}"
DRY_RUN="${DRY_RUN:-0}"
POLL_CMD="${POLL_CMD:-$PY scripts/poll_live_news.py}"
SLEEP_TO_NEXT_WINDOW="${SLEEP_TO_NEXT_WINDOW:-0}"
SLEEP_CAP_SEC="${SLEEP_CAP_SEC:-16200}"
MIN_SLEEP_SEC="${MIN_SLEEP_SEC:-60}"
if [ -n "${GITHUB_ACTIONS:-}" ]; then NO_SYNC="${NO_SYNC:-0}"; else NO_SYNC="${NO_SYNC:-1}"; fi
GUARD="${GUARD:-$HERE/../scripts/guard_news_files.py}"

FILES=(frontdesign/data/_live_news.json
       frontdesign/data/_news_history.json)

if [ -n "${GITHUB_ACTIONS:-}" ]; then
  git config user.name  "stockbot-live[bot]"
  git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
fi

# Reset the working tree to the current tip of main. Discards any local
# leftovers (a half-finished rebase, an unpushed commit, a corrupt file): the
# poller is idempotent, so the most that can cost is one cycle.
sync_to_main() {
  [ "$NO_SYNC" = "1" ] && return 0
  if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then
    git rebase --abort >/dev/null 2>&1 || true
  fi
  if ! git fetch -q origin main; then
    echo "  sync: fetch of origin/main failed; keeping the current tree"; return 1
  fi
  git reset -q --hard FETCH_HEAD
}

commit_and_push() {
  local attempt rc
  for attempt in 1 2 3; do
    if git diff --quiet HEAD -- "${FILES[@]}"; then echo "  no diff; skip commit"; return 0; fi
    if ! "$PY" "$GUARD" "${FILES[@]}"; then
      echo "  guard refused the commit; discarding this cycle's files"
      sync_to_main; return 1
    fi
    if [ "$DRY_RUN" = "1" ]; then echo "  DRY_RUN: would commit ${FILES[*]}"; return 0; fi
    git add "${FILES[@]}"
    git commit -q -m "live: announcements $(TZ=Asia/Kolkata date +%H:%M)" || return 0
    if git push -q origin HEAD:main; then echo "  pushed (attempt $attempt)"; return 0; fi
    echo "  push rejected (attempt $attempt): main moved; re-syncing and re-merging this cycle"
    sync_to_main || return 1
    $POLL_CMD; rc=$?
    if [ "$rc" -eq 2 ]; then echo "  re-poll failed (exit 2); giving up this cycle"; return 1; fi
  done
  echo "  push failed after 3 attempts; the next cycle retries from main"
  sync_to_main
  return 1
}

start=$(date +%s)
iters=0
slept=0
while :; do
  if [ -n "${FORCE_PLAN:-}" ]; then
    read -r action cadence <<< "$FORCE_PLAN"
  else
    read -r action cadence < <("$PY" scripts/poll_schedule.py 2>/dev/null || echo "stop 0")
  fi

  if [ "$action" = "stop" ]; then
    if [ "$SLEEP_TO_NEXT_WINDOW" = "1" ] && [ "$slept" -eq 0 ]; then
      elapsed=$(( $(date +%s) - start ))
      remaining=$(( LINK_DURATION_SEC - elapsed ))
      if [ "$remaining" -lt "$MIN_SLEEP_SEC" ]; then
        echo "[loop] $(date -Iseconds) outside window, link budget spent — handing off"; break
      fi
      next=$("$PY" scripts/poll_schedule.py --next-window 2>/dev/null || echo 0)
      case "$next" in (''|*[!0-9]*) next=0;; esac
      nap=$next
      [ "$nap" -gt "$SLEEP_CAP_SEC" ] && nap=$SLEEP_CAP_SEC
      [ "$nap" -gt "$remaining" ]     && nap=$remaining
      [ "$nap" -lt "$MIN_SLEEP_SEC" ] && nap=$MIN_SLEEP_SEC
      echo "[loop] $(date -Iseconds) outside window — sleeping ${nap}s (next window in ${next}s)"
      slept=1
      sleep "$nap"
      continue
    fi
    echo "[loop] $(date -Iseconds) outside session window — exiting"; break
  fi

  echo "[loop] $(date -Iseconds) poll (cadence=${cadence}s)"
  sync_to_main || true
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
