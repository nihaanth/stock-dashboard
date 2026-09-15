#!/usr/bin/env python3
"""
Append-only archive of NSE corporate announcements, sharded by calendar day.

Layout (frontdesign/data/news/):
    index.json        {"updated_at", "n_total", "days": [{"day", "n", "n_after", "file"}, ...]}
                      days newest first; n_after = filings at/after 15:30 IST
    YYYY-MM-DD.json   {"day", "n_total", "n_after_market", "items": [...]}, items newest first

One shard per day keeps every write small (a poll touches today's shard and the
index -- a few hundred KB -- instead of a multi-MB file) and lets the front-end
fetch only the day it shows, so the archive can grow forever without slowing
the site. Rules every writer follows:

  * items are deduplicated by seq_id; a merge never replaces an existing copy
  * a file that exists but does not parse raises CorruptFeedError and is never
    overwritten -- starting from "empty" on a parse error is how the previous
    single-file archive was wiped seven times in 2026 (docs/news-archive.md)
  * shards only grow; the index is derived from the shards

CLI:
    python scripts/news_archive.py --stats             # what is in the archive
    python scripts/news_archive.py --rebuild-index     # recompute index.json from the shards
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_DIR = ROOT / "frontdesign" / "data" / "news"
INDEX_NAME = "index.json"
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
AFTER_MARKET_CUTOFF = "15:30"   # HH:MM IST; mirrored by AFTER_MARKET_CUTOFF in frontdesign/industries.js
IST = ZoneInfo("Asia/Kolkata")


class CorruptFeedError(RuntimeError):
    """A persisted feed file exists but cannot be parsed. It must never be overwritten."""


@dataclass
class ArchiveUpdate:
    n_added: int                       # fresh items that were not in the archive yet
    n_total: int                       # items in the whole archive afterwards
    days: list[str]                    # every day in the archive, newest first
    touched: list[str] = field(default_factory=list)   # days whose shard was rewritten


# ---------------- item helpers ----------------

def day_of(item: dict) -> str:
    return (item.get("sort_date") or "")[:10]


def is_after_market(item: dict) -> bool:
    sd = item.get("sort_date") or ""
    return len(sd) >= 16 and sd[11:16] >= AFTER_MARKET_CUTOFF


def seq_of(item: dict) -> int | None:
    try:
        return int(item.get("seq_id"))
    except (TypeError, ValueError):
        return None


def _sort_key(item: dict):
    return (item.get("sort_date") or "", seq_of(item) or 0)


def merge_items(existing: Iterable[dict], fresh: Iterable[dict]) -> tuple[list[dict], int]:
    """Union by seq_id: existing copies win, items without a usable seq_id are
    dropped. Returns (merged newest-first, number of fresh items that were new)."""
    seen: set[int] = set()
    merged: list[dict] = []
    for it in existing:
        sid = seq_of(it)
        if sid is None or sid in seen:
            continue
        seen.add(sid)
        merged.append(it)
    added = 0
    for it in fresh:
        sid = seq_of(it)
        if sid is None or sid in seen:
            continue
        seen.add(sid)
        merged.append({**it, "seq_id": sid})
        added += 1
    merged.sort(key=_sort_key, reverse=True)
    return merged, added


# ---------------- files ----------------

def read_json(path: Path) -> Any:
    """Parse a JSON file. Missing -> None. Unparseable -> CorruptFeedError."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        raise CorruptFeedError(
            f"{path} exists but is not valid JSON ({e}); refusing to overwrite it"
        ) from e


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def shard_path(archive_dir: Path, day: str) -> Path:
    return archive_dir / f"{day}.json"


def index_path(archive_dir: Path) -> Path:
    return archive_dir / INDEX_NAME


def load_day(archive_dir: Path, day: str) -> list[dict]:
    """Items of one day's shard. Missing -> []. Corrupt -> CorruptFeedError."""
    data = read_json(shard_path(archive_dir, day))
    if data is None:
        return []
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise CorruptFeedError(
            f"{shard_path(archive_dir, day)} has no items[] list; refusing to overwrite it"
        )
    return items


def load_index(archive_dir: Path) -> dict | None:
    """The index. Missing -> None. Corrupt -> CorruptFeedError."""
    data = read_json(index_path(archive_dir))
    if data is None:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("days"), list):
        raise CorruptFeedError(f"{index_path(archive_dir)} has no days[] list; refusing to overwrite it")
    return data


def list_days(archive_dir: Path) -> list[str]:
    """Days that have a shard on disk, newest first."""
    if not archive_dir.exists():
        return []
    return sorted((p.stem for p in archive_dir.glob("*.json") if DAY_RE.match(p.stem)), reverse=True)


def iter_all_items(archive_dir: Path) -> Iterator[dict]:
    """Every item in the archive, newest day first."""
    for day in list_days(archive_dir):
        yield from load_day(archive_dir, day)


def day_payload(day: str, items: list[dict]) -> dict:
    return {
        "day": day,
        "n_total": len(items),
        "n_after_market": sum(1 for it in items if is_after_market(it)),
        "items": items,
    }


def day_entry(day: str, items: list[dict]) -> dict:
    return {
        "day": day,
        "n": len(items),
        "n_after": sum(1 for it in items if is_after_market(it)),
        "file": f"{day}.json",
    }


def index_payload(entries: dict[str, dict], now: datetime) -> dict:
    days = [entries[d] for d in sorted(entries, reverse=True)]
    return {
        "updated_at": now.isoformat(timespec="seconds"),
        "n_total": sum(int(e.get("n") or 0) for e in days),
        "days": days,
    }


def rebuild_index(archive_dir: Path, now: datetime) -> dict:
    """Recompute index.json from every shard on disk (slow path; used by recovery)."""
    entries = {d: day_entry(d, load_day(archive_dir, d)) for d in list_days(archive_dir)}
    payload = index_payload(entries, now)
    write_json(index_path(archive_dir), payload)
    return payload


# ---------------- the one write path ----------------

def update_archive(fresh_items: Iterable[dict], now: datetime,
                   archive_dir: Path = ARCHIVE_DIR) -> ArchiveUpdate:
    """Merge fresh items into their day shards and refresh the index.

    Fails closed: every file this call could rewrite (the index and each touched
    shard) is parsed BEFORE anything is written, so a corrupt file aborts the
    whole update with the archive untouched. Nothing is written when there is
    nothing new, so a no-op poll produces no diff.
    """
    by_day: dict[str, list[dict]] = {}
    for it in fresh_items:
        d = day_of(it)
        if DAY_RE.match(d) and seq_of(it) is not None:
            by_day.setdefault(d, []).append(it)

    index = load_index(archive_dir)
    current = {d: load_day(archive_dir, d) for d in by_day}
    if index is None:
        entries = {d: day_entry(d, load_day(archive_dir, d)) for d in list_days(archive_dir)}
    else:
        entries = {e["day"]: e for e in index["days"] if isinstance(e, dict) and e.get("day")}
    before = json.dumps([entries[d] for d in sorted(entries)], sort_keys=True)

    n_added = 0
    touched: list[str] = []
    for d in sorted(by_day):
        merged, added = merge_items(current[d], by_day[d])
        if added:
            write_json(shard_path(archive_dir, d), day_payload(d, merged))
            n_added += added
            touched.append(d)
        entries[d] = day_entry(d, merged)

    after = json.dumps([entries[d] for d in sorted(entries)], sort_keys=True)
    if index is None or after != before:
        write_json(index_path(archive_dir), index_payload(entries, now))

    return ArchiveUpdate(
        n_added=n_added,
        n_total=sum(int(e.get("n") or 0) for e in entries.values()),
        days=sorted(entries, reverse=True),
        touched=touched,
    )


# ---------------- CLI ----------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive-dir", type=Path, default=ARCHIVE_DIR)
    ap.add_argument("--rebuild-index", action="store_true", help="recompute index.json from the shards")
    ap.add_argument("--stats", action="store_true", help="print archive totals")
    args = ap.parse_args(argv)
    try:
        if args.rebuild_index:
            idx = rebuild_index(args.archive_dir, datetime.now(IST))
            print(f"[archive] index rebuilt: {idx['n_total']:,} items over {len(idx['days'])} days")
        if args.stats or not args.rebuild_index:
            idx = load_index(args.archive_dir) or {"n_total": 0, "days": []}
            days = [d["day"] for d in idx["days"]]
            print(f"[archive] {idx['n_total']:,} items over {len(days)} days"
                  f" ({days[-1] if days else '-'} .. {days[0] if days else '-'}), "
                  f"{len(list_days(args.archive_dir))} shard files")
    except CorruptFeedError as e:
        print(e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
