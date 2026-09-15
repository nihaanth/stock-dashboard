#!/usr/bin/env python3
"""
Seed the day-sharded news archive (frontdesign/data/news/) from the per-stock
JSON archives.

Per-stock files at frontdesign/data/industries/<slug>/<SYMBOL>.json carry a
news[] array covering ~30-90 days, rebuilt nightly from the webarchive by
scripts/build_industry_folders.py. This script walks them, normalises each
entry to the live-ticker shape (with a synthetic negative seq_id, since
webarchive rows may predate seq_id capture) and merges in whatever the archive
does not already hold, using (symbol, sort_date) as the dedup key.

For disaster recovery use scripts/recover_news_archive.py instead: it rebuilds
the archive from git history. This script is for seeding from per-stock data.

Usage:
    python scripts/backfill_news_history.py [--dry-run] [--since YYYY-MM-DD] [--archive-dir DIR]
"""
from __future__ import annotations

import argparse
import json
import sys
import zlib
from datetime import datetime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore

sys.path.insert(0, str(Path(__file__).resolve().parent))
from news_archive import ARCHIVE_DIR, CorruptFeedError, day_of, load_day, update_archive  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
INDUSTRIES_DIR = ROOT / "frontdesign" / "data" / "industries"
INDUSTRIES_INDEX = INDUSTRIES_DIR / "industries.json"
IST = ZoneInfo("Asia/Kolkata")

SKIP_FILES = {"industries.json", "index.json"}


def load_stock_map() -> dict[str, dict]:
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


def synth_seq_id(symbol: str, sort_date: str, desc: str) -> int:
    key = f"{symbol}|{sort_date}|{desc}".encode("utf-8")
    return -abs(zlib.crc32(key))


def build_record(symbol: str, meta: dict, it: dict) -> dict:
    desc = it.get("desc") or "Announcement"
    return {
        "seq_id": synth_seq_id(symbol, it["sort_date"], desc),
        "symbol": symbol,
        "industry_slug": meta["industry_slug"],
        "name": meta["name"],
        "sort_date": it["sort_date"],
        "an_dt": it.get("an_dt"),
        "desc": desc,
        "sm_name": it.get("sm_name") or meta["name"],
        "attchmntFile": it.get("attchmntFile"),
    }


def iter_per_stock_files():
    for slug_dir in sorted(INDUSTRIES_DIR.iterdir()):
        if not slug_dir.is_dir():
            continue
        for path in sorted(slug_dir.glob("*.json")):
            name = path.name
            if name.startswith("_") or name in SKIP_FILES:
                continue
            yield path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--archive-dir", type=Path, default=ARCHIVE_DIR)
    p.add_argument("--dry-run", action="store_true",
                   help="Compute the merge but don't write anything")
    p.add_argument("--since", type=str, default=None,
                   help="Skip per-stock items with sort_date before YYYY-MM-DD")
    args = p.parse_args()

    if not INDUSTRIES_INDEX.exists():
        print(f"missing {INDUSTRIES_INDEX} — run build_industry_folders.py first",
              file=sys.stderr)
        return 2

    stock_map = load_stock_map()

    files_seen = files_skipped = items_seen = 0
    candidates: dict[str, list[dict]] = {}
    for path in iter_per_stock_files():
        symbol = path.stem
        meta = stock_map.get(symbol)
        if meta is None:
            files_skipped += 1
            continue
        files_seen += 1
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for it in data.get("news") or []:
            items_seen += 1
            sd = it.get("sort_date")
            if not sd:
                continue
            if args.since and sd[:10] < args.since:
                continue
            rec = build_record(symbol, meta, it)
            candidates.setdefault(day_of(rec), []).append(rec)

    try:
        to_add: list[dict] = []
        for day, recs in sorted(candidates.items()):
            keys = {(x.get("symbol"), x.get("sort_date")) for x in load_day(args.archive_dir, day)}
            for r in recs:
                k = (r["symbol"], r["sort_date"])
                if k in keys:
                    continue
                keys.add(k)
                to_add.append(r)
        print(f"backfill: {len(to_add)} new candidate items across {len(candidates)} days, "
              f"files seen={files_seen} (skipped {files_skipped}), items seen={items_seen}")
        if args.dry_run:
            print("  (dry-run — no write)")
            return 0
        upd = update_archive(to_add, datetime.now(IST), args.archive_dir)
    except CorruptFeedError as e:
        print(e, file=sys.stderr)
        return 2

    print(f"  +{upd.n_added} items, {upd.n_total} total over {len(upd.days)} days")
    return 0


if __name__ == "__main__":
    sys.exit(main())
