#!/usr/bin/env python3
"""
Pre-commit guard for the news feed files. Exit 0 = safe to commit, 1 = refuse.

For each file (default: the two live feed files) it checks that
  * the file parses as JSON with an items[] list, and
  * for the append-only archive (_news_history.json), it holds at least as many
    items as the copy committed at --ref (default HEAD). The archive can only
    grow, so a smaller file is a bug or corruption, never data.

Both writers -- automation/live_poll_loop.sh and
.github/workflows/news_backfill.yml -- run this right before `git commit`, so a
regression like the 2026 rebase-conflict wipes (docs/news-archive-wipes.md)
stops on the runner instead of reaching main.

Usage (run from the repository root):
    python scripts/guard_news_files.py                      # both feed files vs HEAD
    python scripts/guard_news_files.py --ref HEAD~1 path.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HISTORY = "frontdesign/data/_news_history.json"
LIVE = "frontdesign/data/_live_news.json"
APPEND_ONLY = {HISTORY}


def load_items(text: str, label: str) -> list:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{label}: not valid JSON ({e})") from e
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError(f"{label}: no items[] list")
    return items


def committed_text(ref: str, rel: str, cwd: Path) -> str | None:
    """Contents of rel at ref, or None if it is not there (first commit)."""
    r = subprocess.run(["git", "show", f"{ref}:{rel}"], capture_output=True, text=True, cwd=cwd)
    return r.stdout if r.returncode == 0 else None


def evaluate(new_text: str, old_text: str | None, rel: str, append_only: bool) -> tuple[bool, str]:
    """Pure decision: (ok, message)."""
    try:
        new_items = load_items(new_text, rel)
    except ValueError as e:
        return False, f"{e}; refusing to commit"
    if not append_only or old_text is None:
        return True, f"{rel}: ok ({len(new_items)} items)"
    try:
        old_items = load_items(old_text, f"{rel} (committed)")
    except ValueError:
        return True, f"{rel}: ok ({len(new_items)} items; committed copy unreadable, no baseline)"
    if len(new_items) < len(old_items):
        return False, f"{rel}: would shrink {len(old_items)} -> {len(new_items)} items; refusing to commit"
    return True, f"{rel}: ok ({len(old_items)} -> {len(new_items)} items)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", help=f"repo-relative paths (default: {HISTORY} {LIVE})")
    ap.add_argument("--ref", default="HEAD", help="git ref holding the baseline copy (default HEAD)")
    ap.add_argument("--root", type=Path, default=Path.cwd(), help="repository root (default: cwd)")
    args = ap.parse_args(argv)

    ok_all = True
    for rel in args.files or [HISTORY, LIVE]:
        path = args.root / rel
        if not path.exists():
            print(f"  guard: {rel}: missing; refusing to commit")
            ok_all = False
            continue
        ok, msg = evaluate(path.read_text(), committed_text(args.ref, rel, args.root),
                           rel, rel in APPEND_ONLY)
        print(f"  guard: {msg}")
        ok_all = ok_all and ok
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
