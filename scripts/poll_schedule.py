"""Decide whether the live poller should poll now, and at what cadence.

Pure, stdlib-only so the loop's timing policy is unit-testable. Windows are IST
(Asia/Kolkata), Mon-Fri:
    08:30-15:30  preopen + main session   -> poll every 180s
    15:30-22:30  after-market filings      -> poll every 600s
    otherwise / weekends                   -> stop

CLI:
    python scripts/poll_schedule.py                ->  "<action> <cadence>"
    python scripts/poll_schedule.py --next-window  ->  seconds until the next
        window opens (integer; 0 when already inside a window)
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

SESSION_START = dtime(8, 30)
SESSION_END = dtime(15, 30)
AFTER_MARKET_END = dtime(22, 30)
MAIN_CADENCE_SEC = 180
AFTER_MARKET_CADENCE_SEC = 600


def session_plan(now: datetime) -> tuple[str, int]:
    """Return (action, cadence_sec); action is 'poll' or 'stop'."""
    if now.weekday() >= 5:
        return ("stop", 0)
    t = now.time()
    if SESSION_START <= t < SESSION_END:
        return ("poll", MAIN_CADENCE_SEC)
    if SESSION_END <= t < AFTER_MARKET_END:
        return ("poll", AFTER_MARKET_CADENCE_SEC)
    return ("stop", 0)


def seconds_until_next_window(now: datetime) -> int:
    """Seconds until the next polling window opens; 0 if already inside one."""
    if session_plan(now)[0] == "poll":
        return 0
    for days in range(8):
        candidate = datetime.combine(
            (now + timedelta(days=days)).date(), SESSION_START, tzinfo=now.tzinfo
        )
        if candidate.weekday() < 5 and candidate > now:
            return math.ceil((candidate - now).total_seconds())
    return 0  # unreachable: a weekday 08:30 always exists within 8 days


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    now = datetime.now(IST)
    if "--next-window" in argv:
        print(seconds_until_next_window(now))
        return 0
    action, cadence = session_plan(now)
    print(f"{action} {cadence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
