#!/usr/bin/env python3
"""
One-time (idempotent) migration: re-point persisted news items at the
current industry slugs/names.

Why: industry slugs/names used to be derived from a stock_master.json whose
strings carried stacked HTML escapes ("Media &amp;amp;... Entertainment" ->
slug "media-amp-amp-...-entertainment"). After the extractor fix and the
industries rebuild, those slugs no longer exist in industries.json, so old
items in the append-only archives would render raw slugs and dead links.

For each of _news_history.json, _after_market_news.json, _live_news.json:
  - rewrite each item's industry_slug + name from the rebuilt stock_index
    (matched by symbol; items whose symbol left the universe are untouched)
  - collapse stacked HTML escapes in sm_name / desc

Usage:
    python scripts/migrate_news_industry_slugs.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

INDUSTRIES_INDEX = ROOT / "frontdesign" / "data" / "industries" / "industries.json"
NEWS_FILES = [
    ROOT / "frontdesign" / "data" / "_news_history.json",
    ROOT / "frontdesign" / "data" / "_after_market_news.json",
    ROOT / "frontdesign" / "data" / "_live_news.json",
]


def unescape_html_fixpoint(s: str) -> str:
    """Collapse stacked html.escape(..., quote=False) layers (each replace
    pass strictly shrinks the string, so the loop always terminates)."""
    while True:
        t = s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        if t == s:
            return t
        s = t


def main() -> None:
    idx = json.loads(INDUSTRIES_INDEX.read_text())
    stock_map = {
        row["symbol"]: {"industry_slug": row["industry_slug"], "name": row.get("name")}
        for row in idx.get("stock_index", [])
        if row.get("symbol")
    }
    print(f"[map] {len(stock_map)} symbols from {INDUSTRIES_INDEX.relative_to(ROOT)}")

    for path in NEWS_FILES:
        if not path.exists():
            print(f"[skip] {path.relative_to(ROOT)} (missing)")
            continue
        payload = json.loads(path.read_text())
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
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        print(f"[done] {path.relative_to(ROOT)}: {len(items)} items, "
              f"{remapped} remapped, {unescaped} fields unescaped, {orphans} orphan symbols")


if __name__ == "__main__":
    main()
