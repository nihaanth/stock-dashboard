#!/usr/bin/env python3
"""
Backfill frontdesign/data/_news_history.json from per-stock JSON archives.

Per-stock files at frontdesign/data/industries/<slug>/<SYMBOL>.json carry a
news[] array covering ~30-90 days, rebuilt nightly from the webarchive by
scripts/build_industry_folders.py. The aggregated _news_history.json was only
introduced today, so historical days are sparse (after-market items only).
This script walks the per-stock files, normalises each news entry to the
live-ticker shape, and merges into the history file using (symbol, sort_date)
as the dedup key.

Usage:
    python scripts/backfill_news_history.py [--dry-run] [--since YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
import sys
import zlib
from collections import Counter
from datetime import datetime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore

ROOT = Path(__file__).resolve().parent.parent
INDUSTRIES_DIR = ROOT / "frontdesign" / "data" / "industries"
INDUSTRIES_INDEX = INDUSTRIES_DIR / "industries.json"
DEFAULT_OUT = ROOT / "frontdesign" / "data" / "_news_history.json"
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


def load_history(out_path: Path) -> dict:
    """Existing archive, or an empty one only if the file does not exist yet.

    A file that exists but does not parse is an error, not an empty archive:
    returning empty here would rewrite the archive with just the per-stock
    items and lose everything else (docs/news-archive-wipes.md).
    """
    if not out_path.exists():
        return {"updated_at": None, "trading_days": [], "n_total": 0, "items": []}
    try:
        data = json.loads(out_path.read_text())
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        raise SystemExit(f"{out_path} exists but is not valid JSON ({e}); "
                         "refusing to overwrite it -- fix or restore it first")
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise SystemExit(f"{out_path} has no items[] list; refusing to overwrite it")
    return data


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--dry-run", action="store_true",
                   help="Compute merge but don't write the output file")
    p.add_argument("--since", type=str, default=None,
                   help="Skip per-stock items with sort_date before YYYY-MM-DD")
    args = p.parse_args()

    if not INDUSTRIES_INDEX.exists():
        print(f"missing {INDUSTRIES_INDEX} — run build_industry_folders.py first",
              file=sys.stderr)
        return 2

    stock_map = load_stock_map()
    history = load_history(args.out)

    merged: list[dict] = list(history.get("items") or [])
    existing_keys: set[tuple[str, str]] = {
        (it.get("symbol"), it.get("sort_date"))
        for it in merged
        if it.get("symbol") and it.get("sort_date")
    }

    files_seen = 0
    files_skipped = 0
    items_seen = 0
    added = 0
    since = args.since

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
        news = data.get("news") or []
        for it in news:
            items_seen += 1
            sd = it.get("sort_date")
            if not sd:
                continue
            if since and sd[:10] < since:
                continue
            key = (symbol, sd)
            if key in existing_keys:
                continue
            merged.append(build_record(symbol, meta, it))
            existing_keys.add(key)
            added += 1

    merged.sort(key=lambda x: x.get("sort_date") or "", reverse=True)

    trading_days: list[str] = []
    seen_days: set[str] = set()
    for it in merged:
        d = (it.get("sort_date") or "")[:10]
        if d and d not in seen_days:
            seen_days.add(d)
            trading_days.append(d)

    now = datetime.now(IST)
    payload = {
        "updated_at": now.isoformat(timespec="seconds"),
        "trading_days": trading_days,
        "n_total": len(merged),
        "items": merged,
    }

    day_counter = Counter((it.get("sort_date") or "")[:10] for it in merged)
    top_days = day_counter.most_common(5)

    print(f"backfill: +{added} items, {len(merged)} total, "
          f"{len(trading_days)} days, files seen={files_seen} (skipped {files_skipped})")
    print("  top 5 days:", ", ".join(f"{d}={n}" for d, n in top_days))

    if args.dry_run:
        print("  (dry-run — no write)")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
