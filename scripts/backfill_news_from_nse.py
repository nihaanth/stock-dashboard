#!/usr/bin/env python3
"""
Heal the dashboard news files by fetching a date RANGE from the NSE
corporate-announcements API and merging it in.

Why this exists:
    scripts/poll_live_news.py only ever fetches *today* (from_date == to_date).
    Any missed poll — a weekend (the poller treats Sat/Sun as "closed"),
    a dropped GitHub Actions run, or a wedged repo — leaves a PERMANENT gap,
    because the poller never re-fetches a past day. This backfill closes such
    gaps over an arbitrary range and re-derives all three front-end files.

It reuses the live poller's exact normalisation + merge logic, so backfilled
items are byte-shaped identically to live-polled ones (same industry_slug from
the current industries.json, same dedup-by-seq_id semantics).

    _news_history.json      append-only archive (Yesterday tab)  — every day kept
    _after_market_news.json rolling 14-day, sort_date >= 15:30 IST (After-Market)
    _live_news.json         today's feed, capped (News tab)

Usage:
    python scripts/backfill_news_from_nse.py [--days 9]
    python scripts/backfill_news_from_nse.py --from 31-05-2026 --to 09-06-2026
    python scripts/backfill_news_from_nse.py --snapshot /tmp/nse_5day_raw.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

# poll_live_news.py is a tracked, stdlib+requests-only module — safe to import.
from poll_live_news import (  # noqa: E402
    DEFAULT_AFTER_MARKET_OUT,
    DEFAULT_HISTORY_OUT,
    DEFAULT_OUT,
    HEADERS,
    IST,
    NSE_API,
    load_stock_map,
    market_state,
    normalise,
    update_after_market,
    update_history,
)

MAX_LIVE_ITEMS = 1000


def fetch_range(frm: str, to: str) -> list[dict]:
    """Fetch NSE announcements for [frm, to] (DD-MM-YYYY). Primes cookies first."""
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        session.get("https://www.nseindia.com/", timeout=12)
    except requests.RequestException:
        pass
    resp = session.get(
        NSE_API, params={"index": "equities", "from_date": frm, "to_date": to}, timeout=40
    )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        data = data["data"]
    return data if isinstance(data, list) else []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=9,
                    help="lookback window in calendar days (default 9 ≈ 5 trading days)")
    ap.add_argument("--from", dest="frm", help="from_date DD-MM-YYYY (overrides --days)")
    ap.add_argument("--to", dest="to", help="to_date DD-MM-YYYY (default today)")
    ap.add_argument("--snapshot", type=Path, help="use a saved NSE JSON array instead of fetching")
    args = ap.parse_args()

    now = datetime.now(IST)

    if args.snapshot:
        raw = json.loads(args.snapshot.read_text())
        print(f"[backfill] loaded {len(raw)} records from snapshot {args.snapshot}")
    else:
        to = args.to or now.strftime("%d-%m-%Y")
        frm = args.frm or (now - dt.timedelta(days=args.days)).strftime("%d-%m-%Y")
        raw = fetch_range(frm, to)
        print(f"[backfill] fetched {len(raw)} NSE records {frm}..{to}")

    stock_map = load_stock_map()
    norm: list[dict] = []
    for rec in raw:
        n = normalise(rec, stock_map)
        if n is not None:
            norm.append(n)
    print(f"[backfill] {len(norm)} universe-filtered, normalised items")

    # 1) Append-only history (Yesterday) — merges, keeps every day.
    hist_total, hist_days = update_history(DEFAULT_HISTORY_OUT, norm, now)
    # 2) After-Market — merges the >=15:30 subset, prunes to last 14 trading days.
    am_total = update_after_market(DEFAULT_AFTER_MARKET_OUT, norm, now)
    # 3) Live (News) — rebuild the feed from the healed history (capped).
    # Use the most recent trading day that actually has announcements, not the
    # raw calendar "today": at the very start of a fresh IST day (pre-market)
    # today is empty, and an empty News desk reads as broken. Showing the latest
    # populated day keeps the feed meaningful; the live poller rolls it over to
    # the new day on its own once that day's filings start arriving.
    hist = json.loads(DEFAULT_HISTORY_OUT.read_text())
    days = sorted({(it.get("sort_date") or "")[:10]
                   for it in hist["items"] if it.get("sort_date")}, reverse=True)
    latest_day = days[0] if days else now.strftime("%Y-%m-%d")
    today_items = [it for it in hist["items"] if (it.get("sort_date") or "")[:10] == latest_day]
    today_items.sort(key=lambda x: x.get("sort_date") or "", reverse=True)
    today_items = today_items[:MAX_LIVE_ITEMS]
    DEFAULT_OUT.write_text(json.dumps({
        "polled_at": now.isoformat(timespec="seconds"),
        "trading_day": latest_day,
        "market_state": market_state(now),
        "n_new_this_poll": 0,
        "n_total": len(today_items),
        "items": today_items,
    }, ensure_ascii=False, indent=2))

    print(f"[backfill] history={hist_total} items / {len(hist_days)} days | "
          f"after_market={am_total} | live_today={len(today_items)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
