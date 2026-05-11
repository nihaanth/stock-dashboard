#!/usr/bin/env python3
"""Refresh data/years data/2026.webarchive with the latest NSE announcements.

In-place update:
  - reads existing seq_ids from the webarchive
  - fetches NSE corporate-announcements API for the last N days (default 30)
  - dedupes by seq_id, appends only new records
  - sorts the file descending by sort_date so newest appear first
  - writes a .bak backup

Usage:
    .venv/bin/python scripts/refresh_webarchive_today.py           # last 30d
    .venv/bin/python scripts/refresh_webarchive_today.py 7         # last 7d
    .venv/bin/python scripts/refresh_webarchive_today.py 60        # last 60d
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.fundamental_analysis import refresh_webarchive, WEBARCHIVE
from scripts.update_webarchive import (
    extract_json_from_webarchive, build_webarchive, parse_sort_date,
)


def main() -> int:
    lookback = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    stats = refresh_webarchive(WEBARCHIVE, lookback_days=lookback)
    print(f"[refresh] {stats}")

    # Re-sort descending by sort_date (newest first) for nicer Safari view
    p = Path(WEBARCHIVE)
    recs = extract_json_from_webarchive(str(p))
    recs.sort(
        key=lambda r: (parse_sort_date(r), r.get("seq_id", "")),
        reverse=True,
    )
    url = "https://www.nseindia.com/api/corporate-announcements?index=equities"
    p.write_bytes(build_webarchive(recs, url))
    if recs:
        print(f"[sort] newest: {recs[0]['symbol']} {recs[0]['sort_date']}")
        print(f"[sort] oldest: {recs[-1]['symbol']} {recs[-1]['sort_date']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
