"""Decide whether the live poller should poll now, and at what cadence.

Pure, stdlib-only so the loop's timing policy is unit-testable. Windows are IST
(Asia/Kolkata), Mon-Fri:
    08:30-15:30  preopen + main session   -> poll every 1200s
    15:30-22:30  after-market filings      -> poll every 1800s
    otherwise / weekends                   -> stop

The cadence is set by the deployment budget, not by how fast NSE publishes.
Every poll that finds something new becomes a commit on main, and every push
to main is one Vercel deployment; the free plan allows 100 a day. At the old
180s/600s cadence the poller pushed 100 to 105 times a day and the site spent
every afternoon rate-limited, refusing to deploy for 24 hours. These windows
cap the poller at MAX_POLLS_PER_DAY (see below), which leaves room for the
gap-backfill crons and for the occasional code change.

Polling less often loses nothing: poll_live_news.py asks NSE for the whole of
today on every call, and the archive dedups by seq_id, so a slower poll simply
returns more new items at once. The cost is freshness -- an announcement shows
up on the site up to one cadence later.

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
MAIN_CADENCE_SEC = 1200
AFTER_MARKET_CADENCE_SEC = 1800

# Vercel's free plan allows 100 deployments a day and each push to main is one.
# MAX_POLLS_PER_DAY is the worst case for these windows: every poll finding
# something new, every weekday. Keep it well under 100.
MAX_POLLS_PER_DAY = (
    (7 * 3600) // MAIN_CADENCE_SEC + (7 * 3600) // AFTER_MARKET_CADENCE_SEC
)


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
