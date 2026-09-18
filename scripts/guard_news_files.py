#!/usr/bin/env python3
"""
Pre-commit guard for the news feed files. Exit 0 = safe to commit, 1 = refuse.

It looks at every file that differs from --ref (default HEAD) under the given
paths -- modified, added, or deleted, staged or not -- and checks:

  * every changed file parses and has the expected shape
    (live feed: items[]; day shard: items[]; index: days[])
  * nothing under the archive is deleted
  * a day shard has at least as many items as the committed copy
  * index.json: n_total does not shrink, no committed day disappears, its counts
    add up, every listed shard exists, and each changed shard's count matches

The archive can only grow, so a smaller file is a bug or corruption, never
data. Both writers (automation/live_poll_loop.sh and
.github/workflows/news_backfill.yml) run this right before `git commit`, so a
regression like the 2026 rebase-conflict wipes (docs/news-archive.md) stops on
the runner instead of reaching main.

Usage (from the repository root):
    python scripts/guard_news_files.py                   # default paths, compare vs HEAD
    python scripts/guard_news_files.py --ref HEAD~1 frontdesign/data/news
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

LIVE = "frontdesign/data/_live_news.json"
ARCHIVE = "frontdesign/data/news"
DEFAULT_PATHS = [LIVE, ARCHIVE]
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")


# ---------------- git plumbing ----------------

def changed_files(ref: str, paths: list[str], root: Path) -> list[tuple[str, str]]:
    """[(rel, status)] for everything under paths that differs from ref, including
    untracked files (reported as 'A'). Renames count as a delete plus an add."""
    out: dict[str, str] = {}
    r = subprocess.run(["git", "diff", "--name-status", ref, "--", *paths],
                       capture_output=True, text=True, cwd=root)
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        status = parts[0][:1]
        if status in ("R", "C") and len(parts) == 3:
            out[parts[1]] = "D"
            out[parts[2]] = "A"
        elif len(parts) >= 2:
            out[parts[1]] = status
    r = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "--", *paths],
                       capture_output=True, text=True, cwd=root)
    for rel in r.stdout.splitlines():
        out.setdefault(rel, "A")
    return sorted(out.items())


def committed_text(ref: str, rel: str, root: Path) -> str | None:
    r = subprocess.run(["git", "show", f"{ref}:{rel}"], capture_output=True, text=True, cwd=root)
    return r.stdout if r.returncode == 0 else None


# ---------------- pure checks ----------------

def parse_feed(text: str, label: str, key: str) -> list:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{label}: not valid JSON ({e})") from e
    seq = data.get(key) if isinstance(data, dict) else None
    if not isinstance(seq, list):
        raise ValueError(f"{label}: no {key}[] list")
    return seq


def kind_of(rel: str) -> str:
    name = Path(rel).name
    if rel == LIVE:
        return "live"
    if rel.startswith(ARCHIVE + "/"):
        if name == "index.json":
            return "index"
        if DAY_RE.match(name):
            return "shard"
    return "other"


def check_shard(rel: str, new_text: str, old_text: str | None) -> tuple[bool, str, int]:
    """(ok, message, item_count)."""
    try:
        items = parse_feed(new_text, rel, "items")
    except ValueError as e:
        return False, str(e), -1
    if old_text is None:
        return True, f"{rel}: ok (new day, {len(items)} items)", len(items)
    try:
        old_items = parse_feed(old_text, f"{rel} (committed)", "items")
    except ValueError:
        return True, f"{rel}: ok ({len(items)} items; committed copy unreadable, no baseline)", len(items)
    if len(items) < len(old_items):
        return False, f"{rel}: would shrink {len(old_items)} -> {len(items)} items", len(items)
    return True, f"{rel}: ok ({len(old_items)} -> {len(items)} items)", len(items)


def check_index(rel: str, new_text: str, old_text: str | None,
                shard_counts: dict[str, int], shard_exists) -> tuple[bool, str]:
    try:
        days = parse_feed(new_text, rel, "days")
        new = json.loads(new_text)
    except ValueError as e:
        return False, str(e)
    by_day: dict[str, int] = {}
    for e in days:
        if not isinstance(e, dict) or not e.get("day"):
            return False, f"{rel}: malformed day entry {e!r}"
        by_day[e["day"]] = int(e.get("n") or 0)
    total = int(new.get("n_total") or 0)
    if total != sum(by_day.values()):
        return False, f"{rel}: n_total {total} != sum of day counts {sum(by_day.values())}"
    for day in by_day:
        if not shard_exists(day):
            return False, f"{rel}: lists {day} but {ARCHIVE}/{day}.json is missing"
    for day, n in shard_counts.items():
        if day in by_day and by_day[day] != n:
            return False, f"{rel}: says {day} has {by_day[day]} items but the shard has {n}"
    if old_text is not None:
        try:
            old = json.loads(old_text)
            old_days = {e["day"]: int(e.get("n") or 0) for e in old.get("days", []) if isinstance(e, dict) and e.get("day")}
            old_total = int(old.get("n_total") or 0)
        except (ValueError, TypeError, AttributeError):
            return True, f"{rel}: ok ({total} items; committed copy unreadable, no baseline)"
        if total < old_total:
            return False, f"{rel}: n_total would shrink {old_total} -> {total}"
        missing = sorted(set(old_days) - set(by_day))
        if missing:
            return False, f"{rel}: committed day(s) would disappear: {', '.join(missing)}"
        return True, f"{rel}: ok ({old_total} -> {total} items, {len(by_day)} days)"
    return True, f"{rel}: ok ({total} items, {len(by_day)} days)"


# ---------------- driver ----------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*", help=f"repo-relative paths (default: {' '.join(DEFAULT_PATHS)})")
    ap.add_argument("--ref", default="HEAD", help="git ref holding the baseline copies (default HEAD)")
    ap.add_argument("--root", type=Path, default=Path.cwd(), help="repository root (default: cwd)")
    args = ap.parse_args(argv)
    paths = args.paths or DEFAULT_PATHS

    changes = changed_files(args.ref, paths, args.root)
    if not changes:
        print("  guard: no changes under", " ".join(paths))
        return 0

    ok_all = True
    shard_counts: dict[str, int] = {}
    index_change: tuple[str, str] | None = None

    def report(ok: bool, msg: str) -> None:
        nonlocal ok_all
        print(f"  guard: {msg}" + ("" if ok else "; refusing to commit"))
        ok_all = ok_all and ok

    for rel, status in changes:
        kind = kind_of(rel)
        if status == "D":
            if kind == "other":
                continue
            report(False, f"{rel}: deleted (archive files are never removed)")
            continue
        path = args.root / rel
        if not path.exists():
            report(False, f"{rel}: missing from the working tree")
            continue
        new_text = path.read_text()
        old_text = committed_text(args.ref, rel, args.root)
        if kind == "live":
            try:
                n = len(parse_feed(new_text, rel, "items"))
                report(True, f"{rel}: ok ({n} items)")
            except ValueError as e:
                report(False, str(e))
        elif kind == "shard":
            ok, msg, n = check_shard(rel, new_text, old_text)
            report(ok, msg)
            if n >= 0:
                shard_counts[Path(rel).stem] = n
        elif kind == "index":
            index_change = (new_text, old_text)
        else:
            report(False, f"{rel}: unexpected file under the archive")

    if index_change is not None:
        rel = f"{ARCHIVE}/index.json"
        ok, msg = check_index(rel, index_change[0], index_change[1], shard_counts,
                              lambda day: (args.root / ARCHIVE / f"{day}.json").exists())
        report(ok, msg)
    elif shard_counts:
        # A shard changed but the index did not: the counts must still agree.
        idx_path = args.root / ARCHIVE / "index.json"
        if idx_path.exists():
            try:
                idx_days = {e["day"]: int(e.get("n") or 0)
                            for e in json.loads(idx_path.read_text()).get("days", []) if e.get("day")}
                stale = [d for d, n in shard_counts.items() if idx_days.get(d) != n]
                if stale:
                    report(False, f"{ARCHIVE}/index.json: not updated for changed shard(s) {', '.join(sorted(stale))}")
            except (ValueError, TypeError, AttributeError):
                report(False, f"{ARCHIVE}/index.json: unreadable")

    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
