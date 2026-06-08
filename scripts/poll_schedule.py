"""Decide whether the live poller should poll now, and at what cadence.

Pure, stdlib-only so the loop's timing policy is unit-testable. Windows are IST
(Asia/Kolkata), Mon-Fri:
    08:30-15:30  preopen + main session   -> poll every 180s
    15:30-22:30  after-market filings      -> poll every 600s
    otherwise / weekends                   -> stop
"""
from __future__ import annotations

from datetime import datetime, time as dtime
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


def main() -> int:
    action, cadence = session_plan(datetime.now(IST))
    print(f"{action} {cadence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
