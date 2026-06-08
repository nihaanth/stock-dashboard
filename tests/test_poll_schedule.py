import importlib.util
import pathlib
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "poll_schedule", ROOT / "scripts" / "poll_schedule.py"
)
ps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ps)

IST = ZoneInfo("Asia/Kolkata")


def _ist(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=IST)


def test_preopen_polls_fast():
    # 2026-06-08 is a Monday
    assert ps.session_plan(_ist(2026, 6, 8, 9, 0)) == ("poll", 180)


def test_main_session_polls_fast():
    assert ps.session_plan(_ist(2026, 6, 8, 10, 0)) == ("poll", 180)


def test_after_market_polls_slow():
    assert ps.session_plan(_ist(2026, 6, 8, 17, 0)) == ("poll", 600)


def test_before_session_stops():
    assert ps.session_plan(_ist(2026, 6, 8, 7, 0)) == ("stop", 0)


def test_late_night_stops():
    assert ps.session_plan(_ist(2026, 6, 8, 23, 0)) == ("stop", 0)


def test_weekend_stops():
    # 2026-06-07 is a Sunday
    assert ps.session_plan(_ist(2026, 6, 7, 10, 0)) == ("stop", 0)


def test_session_start_boundary_polls_fast():
    # exactly 08:30 IST -> fast poll
    assert ps.session_plan(_ist(2026, 6, 8, 8, 30)) == ("poll", 180)


def test_session_end_boundary_polls_slow():
    # exactly 15:30 IST -> after-market (slow) poll, not fast
    assert ps.session_plan(_ist(2026, 6, 8, 15, 30)) == ("poll", 600)


def test_after_market_end_boundary_stops():
    # exactly 22:30 IST -> stop
    assert ps.session_plan(_ist(2026, 6, 8, 22, 30)) == ("stop", 0)
