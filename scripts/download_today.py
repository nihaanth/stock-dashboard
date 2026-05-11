#!/usr/bin/env python3
"""Download today's NSE MTO + Bhavcopy.

Thin wrapper around scripts/download_mto.py + scripts/download_bhavcopy.py
that runs both for today's date. If today is a non-trading day (weekend or
holiday), NSE returns 404; the downloaders skip silently.

Usage:
    .venv/bin/python scripts/download_today.py            # today
    .venv/bin/python scripts/download_today.py 20260509   # specific date
"""
from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "bin" / "python"


def run(target: str) -> int:
    print(f"[*] target date: {target}")
    rc = 0
    for script in ("scripts/download_mto.py", "scripts/download_bhavcopy.py"):
        print(f"\n--- {script} ---")
        cmd = [str(PY), script, "--from", target, "--to", target]
        r = subprocess.call(cmd, cwd=str(ROOT))
        rc |= r
    return rc


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else date.today().strftime("%Y%m%d")
    sys.exit(run(arg))
