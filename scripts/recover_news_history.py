#!/usr/bin/env python3
"""
Rebuild frontdesign/data/_news_history.json as the union of every version of
the file that was ever committed.

Why this works: the archive is append-only, so a committed version that is
SMALLER than its predecessor is never data -- it is a wipe (see
docs/news-archive-wipes.md for the race that caused seven of them in 2026).
Everything the larger version held is still in git. The complete archive is
therefore the union, by seq_id, of the last version before each shrink plus the
current file. Newer versions win when the same seq_id appears twice, so items
that were later re-slugged or un-escaped keep their newest form.

Usage (from the repository root):
    python scripts/recover_news_history.py --dry-run      # report what would be restored
    python scripts/recover_news_history.py                # rewrite the archive in place
    python scripts/recover_news_history.py --ref origin/main --out /tmp/history.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
HISTORY_REL = "frontdesign/data/_news_history.json"
INDUSTRIES_INDEX_REL = "frontdesign/data/industries/industries.json"
IST = ZoneInfo("Asia/Kolkata")


def git(*args: str, cwd: Path, input: str | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          check=True, input=input).stdout


def committed_versions(ref: str, rel: str, cwd: Path) -> list[tuple[str, str, str, int]]:
    """(sha, date, subject, blob_size) for every commit touching rel, newest first."""
    out = git("log", "--format=%H%x00%ci%x00%s", ref, "--", rel, cwd=cwd)
    rows = [line.split("\x00") for line in out.splitlines() if line]
    if not rows:
        return []
    batch = "".join(f"{sha}:{rel}\n" for sha, _, _ in rows)
    sizes = git("cat-file", "--batch-check", cwd=cwd, input=batch).splitlines()
    result = []
    for (sha, date, subject), line in zip(rows, sizes):
        parts = line.split()
        size = int(parts[2]) if len(parts) == 3 and parts[1] == "blob" else -1
        result.append((sha, date, subject, size))
    return result


def select_versions(sizes: list[int]) -> list[int]:
    """Indices worth reading, given blob sizes newest-first: the newest version,
    plus every version whose successor is smaller (the last copy before a shrink).
    False positives (a legitimate small shrink) only cost a little extra reading:
    their items are already present in newer versions and dedup away."""
    keep = [0] if sizes else []
    for i in range(1, len(sizes)):
        if sizes[i] > sizes[i - 1]:
            keep.append(i)
    return keep


def parse_items(text: str) -> list[dict]:
    data = json.loads(text)
    items = data.get("items") if isinstance(data, dict) else None
    return items if isinstance(items, list) else []


def union_versions(versions: list[list[dict]]) -> list[dict]:
    """Merge item lists given newest-first. Exact dedup by seq_id, newest copy wins.
    A synthetic (negative) seq_id, as written by backfill_news_history.py, stands
    in for a real filing; it is dropped when a real record exists for the same
    (symbol, sort_date)."""
    by_seq: dict[int, dict] = {}
    for items in versions:
        for it in items:
            sid = it.get("seq_id")
            try:
                sid = int(sid)
            except (TypeError, ValueError):
                continue
            if sid not in by_seq:
                by_seq[sid] = {**it, "seq_id": sid}
    real_keys = {(it.get("symbol"), it.get("sort_date")) for sid, it in by_seq.items() if sid >= 0}
    merged = [it for sid, it in by_seq.items()
              if sid >= 0 or (it.get("symbol"), it.get("sort_date")) not in real_keys]
    merged.sort(key=lambda x: (x.get("sort_date") or "", x["seq_id"]), reverse=True)
    return merged


def remap_industry(items: list[dict], index_path: Path) -> int:
    """Point items at the current industry slug/name for symbols still in the
    universe (same idea as migrate_news_industry_slugs.py). Returns # changed."""
    if not index_path.exists():
        return 0
    idx = json.loads(index_path.read_text())
    current = {r["symbol"]: r for r in idx.get("stock_index", []) if r.get("symbol")}
    changed = 0
    for it in items:
        row = current.get(it.get("symbol"))
        if not row:
            continue
        slug, name = row.get("industry_slug"), row.get("name") or it.get("name")
        if it.get("industry_slug") != slug or it.get("name") != name:
            it["industry_slug"], it["name"] = slug, name
            changed += 1
    return changed


def build_payload(items: list[dict], now: datetime) -> dict:
    trading_days: list[str] = []
    seen: set[str] = set()
    for it in items:
        d = (it.get("sort_date") or "")[:10]
        if d and d not in seen:
            seen.add(d)
            trading_days.append(d)
    return {
        "updated_at": now.isoformat(timespec="seconds"),
        "trading_days": trading_days,
        "n_total": len(items),
        "items": items,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="HEAD", help="branch/ref whose history to walk (default HEAD)")
    ap.add_argument("--repo", type=Path, default=ROOT, help="repository root")
    ap.add_argument("--out", type=Path, default=None,
                    help=f"output path (default: {HISTORY_REL} inside --repo)")
    ap.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    args = ap.parse_args(argv)
    out = args.out or (args.repo / HISTORY_REL)

    rows = committed_versions(args.ref, HISTORY_REL, args.repo)
    if not rows:
        print(f"no committed versions of {HISTORY_REL} on {args.ref}", file=sys.stderr)
        return 2
    picks = select_versions([r[3] for r in rows])
    print(f"[recover] {len(rows)} commits touch {HISTORY_REL}; reading {len(picks)} versions")

    versions: list[list[dict]] = []
    labels: list[str] = []
    work = args.repo / HISTORY_REL
    if work.exists():
        try:
            versions.append(parse_items(work.read_text()))
            labels.append("working tree")
        except json.JSONDecodeError:
            print("[recover] working-tree copy does not parse; ignoring it")
    for i in picks:
        sha, date, subject, size = rows[i]
        versions.append(parse_items(git("show", f"{sha}:{HISTORY_REL}", cwd=args.repo)))
        labels.append(f"{sha[:8]} {date[:16]} {size:>11,} B  {subject[:48]}")

    # Report what each version contributes, in merge order (newest first).
    seen: set[int] = set()
    for label, items in zip(labels, versions):
        ids = {int(i["seq_id"]) for i in items if i.get("seq_id") is not None}
        new = len(ids - seen)
        seen |= ids
        print(f"  {label:<90} items={len(items):>7,}  new={new:>7,}")

    merged = union_versions(versions)
    remapped = remap_industry(merged, args.repo / INDUSTRIES_INDEX_REL)
    payload = build_payload(merged, datetime.now(IST))
    days = payload["trading_days"]
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    print(f"[recover] result: {len(merged):,} items over {len(days)} days "
          f"({days[-1] if days else '-'} .. {days[0] if days else '-'}), "
          f"{remapped:,} re-slugged, {len(text) / 1e6:.1f} MB")

    if args.dry_run:
        print("[recover] dry-run: nothing written")
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(f"[recover] wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
