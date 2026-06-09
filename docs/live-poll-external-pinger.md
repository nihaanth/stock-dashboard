# Live poll — external morning pinger (manual setup)

## Why this exists

`.github/workflows/live_poll.yml` runs one long, self-healing job that covers the whole IST
trading session **once it starts**. The problem is *starting* it: GitHub's `schedule` event
is best-effort and **drops most scheduled runs**, including the early-morning ones. In
practice the first delivered trigger has been landing ~06:30–08:00 UTC (12:00–13:30 IST), so
the morning session (09:15 IST open) goes uncovered until midday.

`workflow_dispatch`, by contrast, is honored **immediately** and never dropped. So we trigger
the workflow from a **reliable external scheduler** during the morning window. One successful
dispatch is enough — the loop then self-heals the rest of the day.

This is a manual, one-time setup (it needs a GitHub token under your account and a third-party
cron account — neither can be scripted from the repo).

## Step 1 — Create a fine-grained GitHub PAT

GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained tokens →
Generate new token**:

- **Resource owner:** `nihaanth`
- **Repository access:** *Only select repositories* → `stock-dashboard`
- **Permissions → Repository permissions → Actions:** **Read and write** (leave everything
  else as *No access*)
- **Expiration:** your choice (set a calendar reminder to rotate it)

Copy the token (`github_pat_...`). It can do exactly one thing — dispatch Actions on this one
repo — so the blast radius is minimal even though it lives in a third-party service.

## Step 2 — Create the cron job (e.g. cron-job.org)

Sign up at a reliable free scheduler (cron-job.org, EasyCron, Pipedream, etc.) and create a
job with:

- **URL:** `https://api.github.com/repos/nihaanth/stock-dashboard/actions/workflows/live_poll.yml/dispatches`
- **Method:** `POST`
- **Request headers:**
  - `Authorization: Bearer github_pat_...`
  - `Accept: application/vnd.github+json`
  - `X-GitHub-Api-Version: 2022-11-28`
  - `User-Agent: stockbot-pinger`   *(GitHub rejects requests with no User-Agent)*
- **Request body:** `{"ref":"main"}`
- **Schedule (UTC):** every 10 min during **03:00–04:30 UTC, Mon–Fri** (= 08:30–10:00 IST).
  That window is all that's needed — once one dispatch lands, the running loop covers the
  rest of the session. On cron-job.org use a custom schedule: minutes `0,10,20,30,40,50`,
  hours `3`, plus minutes `0,10,20,30` hour `4`, days Mon–Fri.

**Expected response:** HTTP **204 No Content** = success (the dispatch endpoint returns no
body). A `401`/`403` means the token/permission is wrong; `404` usually means the workflow
file name or repo path is off.

## Step 3 — Verify (next trading morning)

```bash
gh run list --workflow live_poll.yml -L 10
```

You should see a `workflow_dispatch` run appear at ~03:00–03:10 UTC, and the first
`live: announcements` commit should land near **09:15–09:30 IST** instead of ~12 IST.

## Notes

- The existing GitHub `schedule` block in `live_poll.yml` is left in place as a harmless
  fallback — no change needed there.
- `concurrency: cancel-in-progress: true` means duplicate dispatches just restart the loop;
  pinging more often than necessary is safe.
- The repo is public, so the extra Actions minutes are free.
