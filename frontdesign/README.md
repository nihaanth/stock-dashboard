# Stockbot Review Dashboard

A static post-mortem dashboard: **what did our models call on the last trading day, and what did the market do on the next trading day?**

Three prediction sources are joined:
- **T**echnical (`ml_pipeline/technical`)
- **M**ulti-horizon (`ml_pipeline/multi_horizon`, h = 1..30)
- **H**orizontal / deliv-spike (`scripts/deliv_spike_scan.py`)

Top 30 stocks per day. Past days stay browsable via the date picker.

## Run locally

Open `index.html` directly — works against `data/sample.json` on first load.

```bash
open frontdesign/index.html
```

## Update with real data

From the project root:

```bash
.venv/bin/python scripts/download_today.py            # today's NSE files
.venv/bin/python scripts/build_dashboard_data.py      # joins → writes frontdesign/data/
```

Then refresh the browser. **No UI code changes needed.**

## Automation (daily, 19:30 IST)

```bash
bash automation/install_launchd.sh
launchctl list | grep stockbot       # verify
```

What happens at 19:30 IST every day:
1. `scripts/download_today.py` — pulls MTO + Bhavcopy
2. `scripts/refresh_webarchive_today.py 7` — refreshes corporate announcements
3. `scripts/build_dashboard_data.py` — builds `predictions_<DATE>.json`, updates `latest.json` and `index.json`
4. `git add … && git commit && git push` — Vercel sees the push and redeploys

Logs: `automation/daily_refresh.log`.

## Deploy to Vercel

One-time setup:

1. From project root: `git init && git add -A && git commit -m "initial"`.
2. Create an empty GitHub repo, then `git remote add origin <url> && git push -u origin main`.
3. On vercel.com → **Import Project** → pick the repo.
4. **Root Directory** → `frontdesign`.
5. Framework Preset → **Other**. No build command. Output dir = `.`.
6. Deploy.

Subsequent days: `daily_refresh.sh` pushes new JSON → Vercel auto-redeploys.

## Data schema

See `data/sample.json` for a full example. Key fields per prediction:

| field | meaning |
|---|---|
| `ticker` | NSE symbol (e.g. `RELIANCE`) |
| `company` | hydrated from `data/nse_equity_list.csv` |
| `sources` | which of `technical / multi / horizontal` flagged this stock |
| `predicted_direction` | `bullish` / `bearish` / `sideways` |
| `previous_close` | close on `prediction_date` (₹) |
| `current_price` | close on `result_date` (₹) |
| `price_change`, `price_change_pct` | the move from prev_close to current |
| `prediction_correct` | did the move match the call? |
| `multi_factor_score` | 0–100 confidence (sources hit + percentile of combined z-score) |
| `technical_signals` | brief tags (model names + delivery ratio) |
| `horizontal_levels.support / .resistance` | two-deep S/R from last 20 trading days |
| `raw` | underlying model outputs (`pred_5d`, `pred_10d`, `spike_ratio`) |
| `notes` | one-line auto-generated explanation |

The top-level wrapper has `prediction_date`, `result_date`, `summary`, and `predictions[]`.

## File layout

```
frontdesign/
  index.html                 ← sticky header + sidebar + main + table
  styles.css                 ← dark/light, finance theme
  app.js                     ← fetch + render + sort + filter
  vercel.json                ← deploy config
  data/
    latest.json              ← points at newest run (overwritten daily)
    index.json               ← all dates + all-time accuracy
    predictions_<DATE>.json  ← one per prediction date (kept forever)
    sample.json              ← fallback so the page renders pre-pipeline
```

## Correctness rules

A call is **correct** when:
- `bullish` → `price_change_pct > +0.25%`
- `bearish` → `price_change_pct < -0.25%`
- `sideways` → `|price_change_pct| <= 0.5%`

Tune at the top of `scripts/build_dashboard_data.py` (`T_BULL`, `T_BEAR`, `T_SIDE`).
