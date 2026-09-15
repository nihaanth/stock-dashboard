"""The poller must never rewrite a feed file it could not parse (that is how the
archive was wiped), and its merge must stay append-only and deduplicated."""
import importlib.util
import json
import pathlib
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("poll_live_news", ROOT / "scripts" / "poll_live_news.py")
pln = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pln)

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 6, 11, 10, 52, tzinfo=IST)
CONFLICTED = (
    '{\n<<<<<<< HEAD\n  "updated_at": "2026-06-11T10:52:00+05:30",\n=======\n'
    '  "updated_at": "2026-06-11T10:47:00+05:30",\n>>>>>>> origin/main\n  "items": []\n}\n'
)


def item(seq, day="2026-06-11", t="10:51:00", sym="TESTCO"):
    return {"seq_id": seq, "symbol": sym, "industry_slug": "banks", "name": "Test Co",
            "sort_date": f"{day} {t}", "an_dt": None, "desc": "General Updates",
            "sm_name": "Test Co", "attchmntFile": None}


def test_update_history_appends_and_dedups_by_seq_id(tmp_path):
    p = tmp_path / "history.json"
    pln.update_history(p, [item(1), item(2, t="09:00:00")], NOW)
    n, days = pln.update_history(p, [item(2, t="09:00:00"), item(3, day="2026-06-10")], NOW)
    data = json.loads(p.read_text())
    assert n == 3 == data["n_total"]
    assert [i["seq_id"] for i in data["items"]] == [1, 2, 3]   # newest first, no duplicate 2
    assert days == ["2026-06-11", "2026-06-10"] == data["trading_days"]


def test_update_history_refuses_to_overwrite_a_corrupt_archive(tmp_path):
    p = tmp_path / "history.json"
    p.write_text(CONFLICTED)
    with pytest.raises(pln.CorruptFeedError):
        pln.update_history(p, [item(1)], NOW)
    assert p.read_text() == CONFLICTED   # untouched


def test_update_history_refuses_an_archive_without_an_items_list(tmp_path):
    p = tmp_path / "history.json"
    p.write_text('{"updated_at": "x"}')
    with pytest.raises(pln.CorruptFeedError):
        pln.update_history(p, [item(1)], NOW)


def test_update_history_accepts_a_preloaded_archive(tmp_path):
    p = tmp_path / "history.json"
    n, _ = pln.update_history(p, [item(5)], NOW, existing=[item(4, day="2026-06-10")])
    assert n == 2


def test_load_previous_rotates_on_a_new_day(tmp_path):
    p = tmp_path / "live.json"
    p.write_text(json.dumps({"trading_day": "2026-06-10", "items": [item(1, day="2026-06-10")]}))
    assert pln.load_previous(p, "2026-06-11") == ([], set())
    items, seen = pln.load_previous(p, "2026-06-10")
    assert len(items) == 1 and seen == {1}


def test_load_previous_refuses_a_corrupt_file(tmp_path):
    p = tmp_path / "live.json"
    p.write_text(CONFLICTED)
    with pytest.raises(pln.CorruptFeedError):
        pln.load_previous(p, "2026-06-11")


def test_main_exits_2_and_writes_nothing_when_a_feed_file_is_corrupt(tmp_path, monkeypatch):
    index = tmp_path / "industries.json"
    index.write_text("{}")
    monkeypatch.setattr(pln, "INDUSTRIES_INDEX", index)
    monkeypatch.setattr(pln, "load_stock_map", lambda: {"TESTCO": {"industry_slug": "banks", "name": "Test Co"}})
    monkeypatch.setattr(pln, "fetch_today", lambda now: pytest.fail("must not fetch when a feed file is corrupt"))
    live, hist = tmp_path / "live.json", tmp_path / "history.json"
    hist.write_text(CONFLICTED)
    monkeypatch.setattr("sys.argv", ["poll_live_news.py", "--out", str(live), "--history-out", str(hist)])

    assert pln.main() == 2
    assert not live.exists()
    assert hist.read_text() == CONFLICTED
