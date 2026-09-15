#!/usr/bin/env python3
"""
One-time (idempotent) migration: re-point persisted news items at the
current industry slugs/names.

Why: industry slugs/names used to be derived from a stock_master.json whose
strings carried stacked HTML escapes ("Media &amp;amp;... Entertainment" ->
slug "media-amp-amp-...-entertainment"). After the extractor fix and the
industries rebuild, those slugs no longer exist in industries.json, so old
items in the append-only archive would render raw slugs and dead links.

For _live_news.json and every day shard under frontdesign/data/news/:
  - rewrite each item's industry_slug + name from the rebuilt stock_index
    (matched by symbol; items whose symbol left the universe are untouched)
  - collapse stacked HTML escapes in sm_name / desc

Usage:
    python scripts/migrate_news_industry_slugs.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from news_archive import ARCHIVE_DIR, list_days, read_json, shard_path, write_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

INDUSTRIES_INDEX = ROOT / "frontdesign" / "data" / "industries" / "industries.json"
LIVE_FILE = ROOT / "frontdesign" / "data" / "_live_news.json"


def unescape_html_fixpoint(s: str) -> str:
    """Collapse stacked html.escape(..., quote=False) layers (each replace
    pass strictly shrinks the string, so the loop always terminates)."""
    while True:
        t = s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        if t == s:
            return t
        s = t


def main() -> None:
    idx = read_json(INDUSTRIES_INDEX)
    stock_map = {
        row["symbol"]: {"industry_slug": row["industry_slug"], "name": row.get("name")}
        for row in idx.get("stock_index", [])
        if row.get("symbol")
    }
    print(f"[map] {len(stock_map)} symbols from {INDUSTRIES_INDEX.relative_to(ROOT)}")

    files = [LIVE_FILE] + [shard_path(ARCHIVE_DIR, d) for d in list_days(ARCHIVE_DIR)]
    for path in files:
        payload = read_json(path)
        if payload is None:
            print(f"[skip] {path.relative_to(ROOT)} (missing)")
            continue
        items = payload.get("items", [])
        remapped = unescaped = orphans = 0
        for it in items:
            meta = stock_map.get(it.get("symbol"))
            if meta:
                if it.get("industry_slug") != meta["industry_slug"] or it.get("name") != meta["name"]:
                    it["industry_slug"] = meta["industry_slug"]
                    it["name"] = meta["name"]
                    remapped += 1
            else:
                orphans += 1
            for k in ("sm_name", "desc"):
                v = it.get(k)
                if isinstance(v, str) and "&" in v:
                    clean = unescape_html_fixpoint(v)
                    if clean != v:
                        it[k] = clean
                        unescaped += 1
        if remapped or unescaped:
            write_json(path, payload)
        print(f"[done] {path.relative_to(ROOT)}: {len(items)} items, "
              f"{remapped} remapped, {unescaped} fields unescaped, {orphans} orphan symbols")


if __name__ == "__main__":
    main()
