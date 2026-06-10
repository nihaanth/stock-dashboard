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


# --- seconds_until_next_window (chain relay nap sizing) ---


def test_next_window_inside_main_session_is_zero():
    assert ps.seconds_until_next_window(_ist(2026, 6, 8, 10, 0)) == 0


def test_next_window_inside_after_market_is_zero():
    assert ps.seconds_until_next_window(_ist(2026, 6, 8, 17, 0)) == 0


def test_next_window_preopen_same_day():
    # Mon 05:00 -> Mon 08:30 = 3.5h
    assert ps.seconds_until_next_window(_ist(2026, 6, 8, 5, 0)) == 12600


def test_next_window_weeknight_after_close():
    # Mon 23:00 -> Tue 08:30 = 9.5h
    assert ps.seconds_until_next_window(_ist(2026, 6, 8, 23, 0)) == 34200


def test_next_window_at_exact_close_boundary():
    # Mon 22:30 -> Tue 08:30 = 10h
    assert ps.seconds_until_next_window(_ist(2026, 6, 8, 22, 30)) == 36000


def test_next_window_friday_night_bridges_to_monday():
    # Fri 2026-06-12 23:00 -> Mon 2026-06-15 08:30 = 57.5h
    assert ps.seconds_until_next_window(_ist(2026, 6, 12, 23, 0)) == 207000


def test_next_window_saturday():
    # Sat 2026-06-13 10:00 -> Mon 08:30 = 46.5h
    assert ps.seconds_until_next_window(_ist(2026, 6, 13, 10, 0)) == 167400


def test_next_window_sunday():
    # Sun 2026-06-07 10:00 -> Mon 08:30 = 22.5h
    assert ps.seconds_until_next_window(_ist(2026, 6, 7, 10, 0)) == 81000


def test_next_window_just_before_open():
    # Mon 08:29 -> 08:30 = 60s
    assert ps.seconds_until_next_window(_ist(2026, 6, 8, 8, 29)) == 60
