# Reliable live NSE news poller — design

**Date:** 2026-06-08
**Status:** Approved design, pending implementation plan

## Context

The live market news view (https://frontdesign.vercel.app/industries#/news) appeared
"not working" on a live trading morning. Investigation showed:

- The browser-side fetch is **healthy**: all three data endpoints return HTTP 200 with
  valid JSON and the page renders. The failure is entirely upstream in the data refresh.
- The data is produced by `scripts/poll_live_news.py`, scheduled by the
  `.github/workflows/live_poll.yml` GitHub Actions cron (`*/5 3-10` + `*/10 11-16`, Mon–Fri).
- **GitHub silently drops ~95% of the scheduled runs.** The cron asks for ~132 runs/day;
  only ~5 actually fire, and the early-session runs are always among those dropped. The
  first successful run of the day lands ~07–08 UTC (12:30–14:00 IST), so the entire
  morning session (09:15 IST open → ~12:45 IST) is never refreshed. That is exactly the
  symptom observed.
- Not a billing issue (with ~5 runs/day, usage is tiny), not a code bug, not the fetch.
- A manual `workflow_dispatch` runs instantly and refreshes the data — proving the script
  and workflow are fine; only the **schedule trigger** is unreliable.

History note: a prior Mac-side launchd poller (`automation/com.stockbot.live.plist`,
`live_refresh.sh`) was abandoned when the Mac moved from IST to US Central — launchd uses
local wall-clock, so it fired in the wrong window. The Mac is not a viable host (wrong
timezone, asleep during IST market hours, 03:45–10:00 UTC).

## Goal

Reliably poll NSE corporate announcements every ~3 minutes during the IST trading session
(and at a lower cadence during the after-market filing window), with coverage that
**starts at market open**, using only GitHub Actions (no external service, no PAT).

## Decisions (settled with user)

1. **Stay in GitHub Actions** — no external cron service, no self-hosted box.
2. **Make the repo public** — `nihaanth/stock-dashboard` → public, for unlimited free
   Actions minutes. (Private repos meter minutes; reliable continuous coverage would be
   ~8,000 min/month vs the 2,000 free tier ≈ $45–50/mo overage. Public removes this.)

## Design

### The core idea

Today's job polls once and exits, so it needs a *trigger every 5 min* (~132/day) — and
GitHub delivers ~5. Flip it: **each triggered job runs a poll loop for a long stretch**,
so only a *handful* of triggers/day are needed, which GitHub reliably delivers. Make those
triggers redundant so dropping is harmless. No PAT, no recursion, no external service.

### 1. Repository visibility

One-time:
```
gh repo edit nihaanth/stock-dashboard --visibility public \
  --accept-visibility-change-consequences
```
Exposes all repo code + full git history (scripts, ML pipeline, the `.docx`, etc.) — not
just the already-public data. Repo **secrets stay secret**. → unlimited free Actions minutes.

### 2. Rework `.github/workflows/live_poll.yml` into a looping job

- **Triggers:** keep `workflow_dispatch`. Replace the dense `*/5`/`*/10` schedule with
  **redundant bootstraps** across the IST window, e.g. `*/10 3-17 * * 1-5` UTC. Most fire
  events are dropped — now harmless, because each job covers a long span and only a few
  successful triggers/day are required.
- **Concurrency:** `concurrency: { group: live-poll, cancel-in-progress: false }` →
  exactly one poll job active at a time. Redundant triggers that fire while a job runs
  become cheap no-op exits (the job's start-time gate sees coverage is already active /
  the window is closed and exits in ~1 min).
- **Looping step (the heart):** replace the single "Poll NSE" + "Commit + push" steps with
  one bounded bash loop:
  ```
  end = now + LINK_DURATION         # ~3 hours (well under the 6h job limit)
  while now < end and within_session_window():
      python scripts/poll_live_news.py
      # commit + push if changed, reusing the existing rebase-with-retries push block
      sleep CADENCE                 # 180s during 09:15–15:30 IST; ~600s after-market
  ```
  `poll_live_news.py` is reused **as-is** (single-shot, exit codes 0/1/2) — the loop and
  commit/push live in the workflow YAML, mirroring the existing `automation/live_refresh.sh`
  commit-on-change pattern and the workflow's current rebase/retry push logic.
- **Session gate:** the loop derives IST time each pass — full ~3-min cadence during the
  main session, ~10-min during the after-market filing window (~16:30–22:25 IST), and it
  stops once the day's window closes so nothing runs overnight.

With ~3-hour links and a redundant ~10-min bootstrap schedule, the ~6 h main session is
tiled with continuous ~3-min polling using only ~2–3 *successful* triggers/day instead of
132 — comfortably within what GitHub delivers, and coverage starts at open.

**Residual limitation (accepted):** if a handoff trigger is dropped, a ≤10-min gap can
occur a few times/day. Zero-gap coverage would require a PAT-based self-dispatch chain;
deliberately **out of scope** (YAGNI) unless gaps prove unacceptable in practice.

### 3. Companion fix — move the live file out of the nightly-wiped directory

`scripts/build_industry_folders.py` does `shutil.rmtree(frontdesign/data/industries/)` on
every daily refresh, then rebuilds it. `_live_news.json` is written *into* that directory,
so a daily-refresh deploy landing before the next poll can briefly 404 the live file. The
after-market and history files were already moved up to `frontdesign/data/` for exactly
this reason (see comment in `industries.js`). Mirror that for the live file:

- `scripts/poll_live_news.py` — `DEFAULT_OUT` → `frontdesign/data/_live_news.json`
- `frontdesign/industries.js` — `LIVE_URL` → `"data/_live_news.json"` (+ update the comment)
- `.github/workflows/live_poll.yml` — `FILES` list path

Separable from the scheduling change; included by default, droppable at plan stage.

## Out of scope (future, if needed)

- PAT-based self-dispatch chain for zero-gap handoffs.
- Trimming/paginating the 17 MB `_news_history.json` (re-fetched every 30s by the page) —
  real but independent performance issue.
- Resolving the local repo's in-progress rebase / detached HEAD (pre-existing, unrelated).

## Verification

1. `gh repo view nihaanth/stock-dashboard --json visibility` → `PUBLIC`.
2. Dispatch the workflow; in the run log, confirm a **single job runs multiple poll
   cycles** (loop iterations with `sleep` between them), not one-and-done.
3. During a live session, confirm `live: announcements HH:MM` commits land every few
   minutes while items are arriving, and none during quiet periods.
4. Confirm the deployed `data/_live_news.json` `polled_at` advances within minutes and
   `market_state` reflects the real session state.
5. Next trading morning: confirm coverage **starts near 09:15 IST** (the behavior broken
   today), e.g. first commit/`polled_at` within ~10 min of open.
6. If Section 3 included: confirm `data/_live_news.json` returns 200, `industries/_live_news.json`
   is no longer referenced, and the file survives a daily-refresh run.
