#!/usr/bin/env python3
"""
Poll NSE corporate-announcements API for the eligible universe and write
a small `_live_news.json` rollup that the front-end polls, plus today's shard
of the day-sharded archive under frontdesign/data/news/ (scripts/news_archive.py).

Reuses the session-prime + headers pattern from scripts/update_webarchive.py.

Usage:
    python scripts/poll_live_news.py
        [--out frontdesign/data/_live_news.json]
        [--archive-dir frontdesign/data/news]
        [--max-items 1000]

Exit codes:
    0  new items added this poll (wrapper should git-commit + push)
    1  no new items (wrapper should skip git operations)
    2  fatal: industries index missing, or an existing feed file does not parse
       (it is left untouched -- never rewritten from scratch)
    NSE fetch failures currently return no items and therefore exit 1.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, time as dtime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # py < 3.9 fallback (unlikely on this Mac)
    from backports.zoneinfo import ZoneInfo  # type: ignore

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from news_archive import (  # noqa: E402
    ARCHIVE_DIR,
    CorruptFeedError,
    load_day,
    load_index,
    read_json,
    update_archive,
)

ROOT = Path(__file__).resolve().parent.parent
INDUSTRIES_INDEX = ROOT / "frontdesign" / "data" / "industries" / "industries.json"
DEFAULT_OUT = ROOT / "frontdesign" / "data" / "_live_news.json"

NSE_API = "https://www.nseindia.com/api/corporate-announcements"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                  "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
}
IST = ZoneInfo("Asia/Kolkata")


def load_stock_map() -> dict[str, dict]:
    """symbol -> {industry_slug, name} for eligible universe."""
    idx = json.loads(INDUSTRIES_INDEX.read_text())
    out: dict[str, dict] = {}
    for row in idx.get("stock_index", []):
        sym = row.get("symbol")
        if sym:
            out[sym] = {
                "industry_slug": row.get("industry_slug"),
                "name": row.get("name") or sym,
            }
    return out


def load_previous(out_path: Path, trading_day: str) -> tuple[list[dict], set[int]]:
    """Return (items_today, seen_seq_ids). Rotate if the file is from a prior day.

    A file that exists but does not parse raises CorruptFeedError rather than
    being treated as empty: the caller must not rewrite it.
    """
    prev = read_json(out_path)
    if prev is None:
        return [], set()
    if not isinstance(prev, dict):
        raise CorruptFeedError(f"{out_path} is not a JSON object; refusing to overwrite it")
    if prev.get("trading_day") != trading_day:
        # Different day — start fresh.
        return [], set()
    items = prev.get("items", []) or []
    return items, {int(i["seq_id"]) for i in items if "seq_id" in i}


def market_state(now: datetime) -> str:
    """Crude market-state classifier. NSE: 09:00–09:15 preopen, 09:15–15:30 open, else closed."""
    if now.weekday() >= 5:
        return "closed"
    t = now.time()
    if dtime(9, 0) <= t < dtime(9, 15):
        return "preopen"
    if dtime(9, 15) <= t < dtime(15, 30):
        return "open"
    return "closed"


def fetch_today(now: datetime) -> list[dict]:
    """Hit NSE for today's announcements. Returns [] on any failure."""
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        session.get("https://www.nseindia.com/", timeout=10)
    except requests.RequestException:
        pass
    today_str = now.strftime("%d-%m-%Y")
    params = {"index": "equities", "from_date": today_str, "to_date": today_str}
    try:
        resp = session.get(NSE_API, params=params, timeout=20)
    except requests.RequestException as e:
        print(f"  nse request failed: {e}", file=sys.stderr)
        return []
    if resp.status_code != 200:
        print(f"  nse status {resp.status_code}", file=sys.stderr)
        return []
    try:
        data = resp.json()
    except ValueError as e:
        print(f"  nse json parse failed: {e}", file=sys.stderr)
        return []
    if not isinstance(data, list):
        # Some NSE endpoints wrap in {"data": [...]} — defensive
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return data["data"]
        return []
    return data


def normalise(rec: dict, stock_map: dict[str, dict]) -> dict | None:
    """Map an NSE record into our front-end-friendly shape. Returns None if record is unusable."""
    sym = rec.get("symbol")
    if not sym or sym not in stock_map:
        return None
    seq_id = rec.get("seq_id")
    sort_date = rec.get("sort_date")
    if not seq_id or not sort_date:
        return None
    meta = stock_map[sym]
    return {
        "seq_id": int(seq_id),
        "symbol": sym,
        "industry_slug": meta["industry_slug"],
        "name": meta["name"],
        "sort_date": sort_date,
        "an_dt": rec.get("an_dt"),
        "desc": rec.get("desc") or rec.get("smName") or "Announcement",
        "sm_name": rec.get("sm_name") or meta["name"],
        "attchmntFile": rec.get("attchmntFile"),
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--archive-dir", type=Path, default=ARCHIVE_DIR)
    p.add_argument("--max-items", type=int, default=1000)
    args = p.parse_args()

    if not INDUSTRIES_INDEX.exists():
        print(f"  missing {INDUSTRIES_INDEX} — run build_industry_folders.py first", file=sys.stderr)
        return 2

    now = datetime.now(IST)
    trading_day = now.strftime("%Y-%m-%d")
    state = market_state(now)

    stock_map = load_stock_map()
    # Everything this run may rewrite must parse BEFORE we fetch or write anything,
    # so a corrupt file aborts with nothing touched (exit 2).
    try:
        prev_items, seen = load_previous(args.out, trading_day)
        load_index(args.archive_dir)
        load_day(args.archive_dir, trading_day)
    except CorruptFeedError as e:
        print(f"  {e}", file=sys.stderr)
        return 2

    fresh = fetch_today(now)
    new_items: list[dict] = []
    for rec in fresh:
        norm = normalise(rec, stock_map)
        if norm is None:
            continue
        if norm["seq_id"] in seen:
            continue
        # Compare on the date prefix, not the full timestamp: a date-only
        # sort_date ("2026-06-10") would sort < "2026-06-10 00:00:00" and be
        # wrongly dropped as "before today".
        if norm["sort_date"][:10] < trading_day:
            continue
        new_items.append(norm)
        seen.add(norm["seq_id"])

    # merge: prepend new (desc by sort_date), then existing
    merged = new_items + prev_items
    merged.sort(key=lambda x: x.get("sort_date") or "", reverse=True)
    merged = merged[: args.max_items]

    try:
        archive = update_archive(merged, now, args.archive_dir)
    except CorruptFeedError as e:
        print(f"  {e}", file=sys.stderr)
        return 2

    payload = {
        "polled_at": now.isoformat(timespec="seconds"),
        "trading_day": trading_day,
        "market_state": state,
        "n_new_this_poll": len(new_items),
        "n_total": len(merged),
        "items": merged,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    print(f"[poll] {now.isoformat(timespec='seconds')} state={state} "
          f"fetched={len(fresh)} new={len(new_items)} total={len(merged)} "
          f"archive={archive.n_total}/{len(archive.days)}d (+{archive.n_added})")

    return 0 if new_items else 1


if __name__ == "__main__":
    sys.exit(main())
