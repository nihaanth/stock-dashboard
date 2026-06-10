# Reliable Live NSE News Poller — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the live NSE announcement poller refresh reliably from market open by running a long-lived, self-healing GitHub Actions loop instead of depending on per-5-min scheduled runs that GitHub drops.

**Architecture:** A single GitHub Actions job runs `automation/live_poll_loop.sh`, which polls `scripts/poll_live_news.py` on a cadence decided by a new pure helper `scripts/poll_schedule.py`, committing the live data files when they change. A redundant `schedule` plus `concurrency: cancel-in-progress: true` means any delivered trigger (re)starts a loop that covers up to ~5h, so dropped triggers no longer create gaps. The repo is made public so Actions minutes are free.

**Tech Stack:** GitHub Actions (YAML), Bash, Python 3.11 (stdlib `datetime`/`zoneinfo`), pytest.

---

## Pre-requisites (do before Task 1)

The local working tree is **mid-rebase on a detached HEAD**. Resolve it first, then branch:

```bash
cd /Users/nihaanthreddy/devl/Learning/Projects/stockbot
git rebase --abort            # or: git rebase --continue, if you know the rebase state
git switch -c reliable-live-poller   # land on a real branch off your intended base
.venv/bin/python -m pytest --version || .venv/bin/pip install pytest
```

All `git commit` steps below assume you are on the `reliable-live-poller` branch.

---

## File Structure

- **Create** `scripts/poll_schedule.py` — pure helper: given an IST `datetime`, return `(action, cadence_sec)`. Single responsibility: timing policy. Importable + a CLI used by the loop.
- **Create** `tests/test_poll_schedule.py` — pytest for the window boundaries.
- **Create** `automation/live_poll_loop.sh` — the polling loop + commit/push (mirrors `automation/live_refresh.sh`). Single responsibility: orchestration.
- **Modify** `.github/workflows/live_poll.yml` — triggers, concurrency, single step that runs the loop script.
- **Modify** `scripts/poll_live_news.py:35` — move `DEFAULT_OUT` out of the nightly-wiped dir.
- **Modify** `frontdesign/industries.js:14-16` — point `LIVE_URL` at the new path; fix the comment.

---

## Task 1: Move the live file out of the nightly-wiped directory (Section 3)

`scripts/build_industry_folders.py:293` does `shutil.rmtree(frontdesign/data/industries/)` nightly. The live file must live in `frontdesign/data/` (a sibling), like the after-market/history files already do.

**Files:**
- Modify: `scripts/poll_live_news.py:35`
- Modify: `frontdesign/industries.js:14-16`
- Remove (if tracked): `frontdesign/data/industries/_live_news.json`

- [ ] **Step 1: Repoint the Python default output path**

In `scripts/poll_live_news.py`, change line 35 from:

```python
DEFAULT_OUT = ROOT / "frontdesign" / "data" / "industries" / "_live_news.json"
```

to:

```python
DEFAULT_OUT = ROOT / "frontdesign" / "data" / "_live_news.json"
```

- [ ] **Step 2: Repoint the front-end fetch URL + fix the comment**

In `frontdesign/industries.js`, replace lines 14-16:

```javascript
const LIVE_URL = "data/industries/_live_news.json";
// Sibling of latest.json — not under data/industries/ because build_industry_folders.py
// wipes that directory nightly and would prune the rolling 2-day cache.
```

with:

```javascript
// Sibling of latest.json — not under data/industries/ because build_industry_folders.py
// wipes that directory nightly (shutil.rmtree), which would 404 the live feed.
const LIVE_URL = "data/_live_news.json";
const AFTER_MARKET_URL = "data/_after_market_news.json";
const HISTORY_URL = "data/_news_history.json";
```

NOTE: lines 17-18 already define `AFTER_MARKET_URL`/`HISTORY_URL` — delete the old duplicate lines 17-18 so they are not defined twice. After the edit, confirm each `const` appears exactly once: `grep -n "AFTER_MARKET_URL\|HISTORY_URL\|LIVE_URL" frontdesign/industries.js`.

- [ ] **Step 3: Verify the poller writes to the new path**

Run:

```bash
.venv/bin/python scripts/poll_live_news.py --out /tmp/live_test.json --after-market-out /tmp/am_test.json --history-out /tmp/hist_test.json
test -f /tmp/live_test.json && python3 -c "import json;d=json.load(open('/tmp/live_test.json'));print('ok', d['market_state'], len(d['items']))"
```

Expected: prints `ok <state> <n>` (network permitting; `[poll] ... state=...` line on stderr). Confirms the script still runs and writes a live file.

- [ ] **Step 4: Confirm default path resolves correctly**

Run:

```bash
.venv/bin/python -c "import importlib.util,pathlib; r=pathlib.Path('.').resolve(); s=importlib.util.spec_from_file_location('p','scripts/poll_live_news.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print(m.DEFAULT_OUT)"
```

Expected: path ending in `frontdesign/data/_live_news.json` (no `/industries/`).

- [ ] **Step 5: Remove the now-orphaned old file (if tracked)**

```bash
git rm --ignore-unmatch frontdesign/data/industries/_live_news.json
```

- [ ] **Step 6: Commit**

```bash
git add scripts/poll_live_news.py frontdesign/industries.js
git commit -m "fix: move _live_news.json out of nightly-wiped industries dir"
```

---

## Task 2: Add the testable polling-cadence helper

**Files:**
- Create: `scripts/poll_schedule.py`
- Test: `tests/test_poll_schedule.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_poll_schedule.py`:

```python
import importlib.util
import pathlib
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "poll_schedule", ROOT / "scripts" / "poll_schedule.py"
)
ps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ps)

IST = ZoneInfo("Asia/Kolkata")


def _ist(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=IST)


def test_preopen_polls_fast():
    # 2026-06-08 is a Monday
    assert ps.session_plan(_ist(2026, 6, 8, 9, 0)) == ("poll", 180)


def test_main_session_polls_fast():
    assert ps.session_plan(_ist(2026, 6, 8, 10, 0)) == ("poll", 180)


def test_after_market_polls_slow():
    assert ps.session_plan(_ist(2026, 6, 8, 17, 0)) == ("poll", 600)


def test_before_session_stops():
    assert ps.session_plan(_ist(2026, 6, 8, 7, 0)) == ("stop", 0)


def test_late_night_stops():
    assert ps.session_plan(_ist(2026, 6, 8, 23, 0)) == ("stop", 0)


def test_weekend_stops():
    # 2026-06-07 is a Sunday
    assert ps.session_plan(_ist(2026, 6, 7, 10, 0)) == ("stop", 0)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_poll_schedule.py -v`
Expected: FAIL — `FileNotFoundError`/import error because `scripts/poll_schedule.py` does not exist yet.

- [ ] **Step 3: Write the minimal implementation**

Create `scripts/poll_schedule.py`:

```python
"""Decide whether the live poller should poll now, and at what cadence.

Pure, stdlib-only so the loop's timing policy is unit-testable. Windows are IST
(Asia/Kolkata), Mon-Fri:
    08:30-15:30  preopen + main session   -> poll every 180s
    15:30-22:30  after-market filings      -> poll every 600s
    otherwise / weekends                   -> stop
"""
from __future__ import annotations

from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

SESSION_START = dtime(8, 30)
SESSION_END = dtime(15, 30)
AFTER_MARKET_END = dtime(22, 30)
MAIN_CADENCE_SEC = 180
AFTER_MARKET_CADENCE_SEC = 600


def session_plan(now: datetime) -> tuple[str, int]:
    """Return (action, cadence_sec); action is 'poll' or 'stop'."""
    if now.weekday() >= 5:
        return ("stop", 0)
    t = now.time()
    if SESSION_START <= t < SESSION_END:
        return ("poll", MAIN_CADENCE_SEC)
    if SESSION_END <= t < AFTER_MARKET_END:
        return ("poll", AFTER_MARKET_CADENCE_SEC)
    return ("stop", 0)


def main() -> int:
    action, cadence = session_plan(datetime.now(IST))
    print(f"{action} {cadence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_poll_schedule.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Verify the CLI prints what the loop expects**

Run: `.venv/bin/python scripts/poll_schedule.py`
Expected: one line, either `poll 180`, `poll 600`, or `stop 0` (depending on current IST time).

- [ ] **Step 6: Commit**

```bash
git add scripts/poll_schedule.py tests/test_poll_schedule.py
git commit -m "feat: add testable IST poll-cadence helper"
```

---

## Task 3: Add the polling loop script

**Files:**
- Create: `automation/live_poll_loop.sh`

- [ ] **Step 1: Write the loop script**

Create `automation/live_poll_loop.sh`:

```bash
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
  if git diff --quiet -- "${FILES[@]}"; then echo "  no diff; skip commit"; return 0; fi
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
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x automation/live_poll_loop.sh`

- [ ] **Step 3: Smoke-test loop control with no network and no commits**

Run:

```bash
FORCE_PLAN="poll 1" POLL_CMD=true DRY_RUN=1 MAX_ITERS=1 bash automation/live_poll_loop.sh
```

Expected output contains `[loop] ... poll (cadence=1s)`, then `  no diff; skip commit` (or `DRY_RUN: would commit`), then `[loop] reached MAX_ITERS=1 — exiting`. No git commit is created (verify with `git status` — clean).

- [ ] **Step 4: Smoke-test the stop path**

Run:

```bash
FORCE_PLAN="stop 0" POLL_CMD=true bash automation/live_poll_loop.sh
```

Expected: prints `outside session window — exiting` and runs zero polls.

- [ ] **Step 5: Commit**

```bash
git add automation/live_poll_loop.sh
git commit -m "feat: add self-healing live poll loop script"
```

---

## Task 4: Rewrite the workflow to use the loop

**Files:**
- Modify: `.github/workflows/live_poll.yml` (full replacement)

- [ ] **Step 1: Replace the workflow file contents**

Overwrite `.github/workflows/live_poll.yml` with:

```yaml
name: live · NSE announcement poll

# Reliable intra-day poller. ONE long-running job loops on a cadence and commits
# frontdesign/data/_live_news.json (+ after-market/history) to main; Vercel
# auto-deploys on push. GitHub drops most scheduled runs, so we no longer depend
# on any single one: the schedule fires redundantly, and cancel-in-progress means
# a freshly-delivered trigger simply restarts/extends coverage. Each job covers up
# to ~5h, so even a single delivered trigger bridges long stretches of dropped ones.
#
#   */5  3-4  * * 1-5 UTC  ≈ dense bootstrap, 08:30-10:29 IST (get a loop alive at open)
#   */10 3-17 * * 1-5 UTC  ≈ every 10 min, 08:30-23:29 IST (redundant all-day coverage)
# automation/live_poll_loop.sh self-classifies the IST window via
# scripts/poll_schedule.py and exits outside 08:30-22:30 IST, so nothing runs overnight.

on:
  schedule:
    - cron: "*/5 3-4 * * 1-5"
    - cron: "*/10 3-17 * * 1-5"
  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: live-poll
  cancel-in-progress: true

jobs:
  poll:
    runs-on: ubuntu-latest
    timeout-minutes: 350   # ceiling only; the loop self-terminates at window close / 5h
    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          ref: main
          token: ${{ secrets.GITHUB_TOKEN }}

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install requests

      - name: Poll loop
        run: bash automation/live_poll_loop.sh
```

- [ ] **Step 2: Validate the YAML**

Run:

```bash
.venv/bin/python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/live_poll.yml')); print('yaml ok')"
```

Expected: `yaml ok`. (If `actionlint` is installed, also run `actionlint .github/workflows/live_poll.yml`.)

- [ ] **Step 3: Confirm the workflow references real paths**

Run:

```bash
test -f automation/live_poll_loop.sh && test -f scripts/poll_schedule.py && echo "referenced files exist"
```

Expected: `referenced files exist`.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/live_poll.yml
git commit -m "feat: drive live poll via long-running self-healing job"
```

---

## Task 5: Make the repo public and verify end-to-end

**Files:** none (operational).

- [ ] **Step 1: Merge the branch to main**

```bash
git switch main && git merge --ff-only reliable-live-poller && git push origin main
```

(If you cannot fast-forward, open a PR and merge it — the workflow must exist on `main` to be scheduled.)

- [ ] **Step 2: Make the repository public**

```bash
gh repo edit nihaanth/stock-dashboard --visibility public --accept-visibility-change-consequences
gh repo view nihaanth/stock-dashboard --json visibility
```

Expected: `{"visibility":"PUBLIC"}`. This grants unlimited free Actions minutes.

- [ ] **Step 3: Dispatch a run and confirm the loop iterates**

```bash
gh workflow run live_poll.yml
sleep 20 && gh run list --workflow live_poll.yml -L 1
RUN=$(gh run list --workflow live_poll.yml -L 1 --json databaseId -q '.[0].databaseId')
gh run watch "$RUN" --interval 10 || true
gh run view "$RUN" --log | grep "\[loop\]" | head
```

Expected: the log shows **multiple** `[loop] ... poll` lines from a single run (not one-and-done). During a live session it also commits `live: announcements HH:MM`.

- [ ] **Step 4: Confirm the deployed live file is fresh**

```bash
curl -s "https://frontdesign.vercel.app/data/_live_news.json?t=$(date +%s)" \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['polled_at'],d['market_state'],len(d['items']))"
```

Expected: `polled_at` is today and within a few minutes; `market_state` matches the real IST session. (Allow ~1 min for Vercel deploy + 30s CDN cache.)

- [ ] **Step 5: Next-trading-morning check (manual, the real acceptance test)**

The morning after deploy, confirm the first commit / `polled_at` lands within ~10–15 min of 09:15 IST — i.e., the morning session is covered, which is the behaviour broken today.

---

## Self-Review notes

- **Spec coverage:** repo-public (Task 5.2), looping job + redundant schedule + concurrency (Tasks 3–4), session/cadence gate (Task 2), reuse of `poll_live_news.py` as-is (only `DEFAULT_OUT` path changes, Task 1), Section 3 path move (Task 1), verification (Task 5) — all covered. Out-of-scope items (PAT self-chain, 17 MB history trim) intentionally omitted.
- **Naming consistency:** `session_plan(now) -> (action, cadence)` defined in Task 2 and consumed verbatim by `live_poll_loop.sh` in Task 3 (`read -r action cadence`). `FILES` array path matches the new `DEFAULT_OUT` from Task 1.
- **Residual gap:** if GitHub delivers no trigger for the morning until late, coverage still starts late — bounded by GitHub's first-delivery latency. The dense `*/5 3-4` bootstrap minimizes this; full elimination needs the out-of-scope PAT chain.
```
