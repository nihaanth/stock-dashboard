#!/usr/bin/env python3
"""
Heal the news archive by fetching a date RANGE from the NSE
corporate-announcements API and merging it in.

Why this exists:
    scripts/poll_live_news.py only ever fetches *today* (from_date == to_date).
    Any missed poll — a weekend (the poller treats Sat/Sun as "closed"),
    a dropped GitHub Actions run, or a wedged repo — leaves a PERMANENT gap,
    because the poller never re-fetches a past day. This backfill closes such
    gaps over an arbitrary range and re-derives the front-end files.

It reuses the live poller's exact normalisation and the shared archive writer
(scripts/news_archive.py), so backfilled items are shaped identically to
live-polled ones (same industry_slug from the current industries.json, same
dedup-by-seq_id semantics).

    frontdesign/data/news/<day>.json + index.json   append-only archive (After-Market + Yesterday tabs)
    frontdesign/data/_live_news.json                latest day's feed, capped (News tab)

Usage:
    python scripts/backfill_news_from_nse.py [--days 9]
    python scripts/backfill_news_from_nse.py --from 31-05-2026 --to 09-06-2026
    python scripts/backfill_news_from_nse.py --snapshot /tmp/nse_5day_raw.json

Exit codes (so a scheduler can gate the git commit and avoid timestamp-only churn):
    0  new items were added to the archive (caller should commit + push)
    1  no new items (files unchanged except timestamps — caller should skip commit)
    2  fatal (NSE fetch failed / returned nothing / a feed file does not parse)
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
    DEFAULT_OUT,
    HEADERS,
    IST,
    NSE_API,
    load_stock_map,
    market_state,
    normalise,
)
from news_archive import ARCHIVE_DIR, load_day, update_archive  # noqa: E402

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
    ap.add_argument("--archive-dir", type=Path, default=ARCHIVE_DIR)
    ap.add_argument("--live-out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    now = datetime.now(IST)

    if args.snapshot:
        raw = json.loads(args.snapshot.read_text())
        print(f"[backfill] loaded {len(raw)} records from snapshot {args.snapshot}")
    else:
        to = args.to or now.strftime("%d-%m-%Y")
        frm = args.frm or (now - dt.timedelta(days=args.days)).strftime("%d-%m-%Y")
        try:
            raw = fetch_range(frm, to)
        except requests.RequestException as e:
            print(f"[backfill] FATAL: NSE fetch failed: {e}", file=sys.stderr)
            return 2
        print(f"[backfill] fetched {len(raw)} NSE records {frm}..{to}")
    if not raw:
        print("[backfill] FATAL: no records returned", file=sys.stderr)
        return 2

    stock_map = load_stock_map()
    norm: list[dict] = []
    for rec in raw:
        n = normalise(rec, stock_map)
        if n is not None:
            norm.append(n)
    print(f"[backfill] {len(norm)} universe-filtered, normalised items")

    # 1) Append-only archive (After-Market + Yesterday tabs) — merges per day, keeps every day.
    #    n_added gates the caller's commit so a no-op run doesn't churn timestamps into git.
    archive = update_archive(norm, now, args.archive_dir)

    # 2) Live (News) — rebuild the feed from the healed archive (capped).
    # Use the most recent day that actually has announcements, not the raw
    # calendar "today": at the very start of a fresh IST day (pre-market) today
    # is empty, and an empty News desk reads as broken. Showing the latest
    # populated day keeps the feed meaningful; the live poller rolls it over to
    # the new day on its own once that day's filings start arriving.
    latest_day = archive.days[0] if archive.days else now.strftime("%Y-%m-%d")
    today_items = load_day(args.archive_dir, latest_day)
    today_items.sort(key=lambda x: x.get("sort_date") or "", reverse=True)
    today_items = today_items[:MAX_LIVE_ITEMS]
    args.live_out.parent.mkdir(parents=True, exist_ok=True)
    args.live_out.write_text(json.dumps({
        "polled_at": now.isoformat(timespec="seconds"),
        "trading_day": latest_day,
        "market_state": market_state(now),
        "n_new_this_poll": 0,
        "n_total": len(today_items),
        "items": today_items,
    }, ensure_ascii=False, indent=2))

    print(f"[backfill] archive={archive.n_total} items / {len(archive.days)} days | "
          f"live_{latest_day}={len(today_items)} | new_to_archive={archive.n_added}")
    return 0 if archive.n_added else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise  # preserve main()'s 0/1/2 exit codes
    except Exception as e:
        # Any unhandled error is FATAL (2), not "no new items" (1) — exiting 1
        # would tell the scheduler to skip the commit and silently leave the gap.
        print(f"[backfill] FATAL: {e}", file=sys.stderr)
        sys.exit(2)
