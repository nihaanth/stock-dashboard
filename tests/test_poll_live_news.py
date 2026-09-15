"""The poller must never rewrite a feed file it could not parse, and it writes
today's shard + the index through the shared archive writer."""
import json
from datetime import datetime

import pytest

import news_archive as na
import poll_live_news as pln

CONFLICTED = (
    '{\n<<<<<<< HEAD\n  "updated_at": "2026-06-11T10:52:00+05:30",\n=======\n'
    '  "updated_at": "2026-06-11T10:47:00+05:30",\n>>>>>>> origin/main\n  "items": []\n}\n'
)


def item(seq, day="2026-06-11", t="10:51:00", sym="TESTCO"):
    return {"seq_id": seq, "symbol": sym, "industry_slug": "banks", "name": "Test Co",
            "sort_date": f"{day} {t}", "an_dt": None, "desc": "General Updates",
            "sm_name": "Test Co", "attchmntFile": None}


def nse_record(seq, when):
    return {"symbol": "TESTCO", "seq_id": str(seq), "sort_date": when, "an_dt": None,
            "desc": "General Updates", "sm_name": "Test Co", "attchmntFile": None}


def test_load_previous_rotates_on_a_new_day(tmp_path):
    p = tmp_path / "live.json"
    p.write_text(json.dumps({"trading_day": "2026-06-10", "items": [item(1, day="2026-06-10")]}))
    assert pln.load_previous(p, "2026-06-11") == ([], set())
    items, seen = pln.load_previous(p, "2026-06-10")
    assert len(items) == 1 and seen == {1}


def test_load_previous_refuses_a_corrupt_file(tmp_path):
    p = tmp_path / "live.json"
    p.write_text(CONFLICTED)
    with pytest.raises(na.CorruptFeedError):
        pln.load_previous(p, "2026-06-11")


@pytest.fixture
def poller_env(tmp_path, monkeypatch):
    index = tmp_path / "industries.json"
    index.write_text("{}")
    monkeypatch.setattr(pln, "INDUSTRIES_INDEX", index)
    monkeypatch.setattr(pln, "load_stock_map", lambda: {"TESTCO": {"industry_slug": "banks", "name": "Test Co"}})
    live, archive = tmp_path / "live.json", tmp_path / "news"
    monkeypatch.setattr("sys.argv", ["poll_live_news.py", "--out", str(live), "--archive-dir", str(archive)])
    return live, archive


def test_main_writes_the_live_feed_and_todays_shard(poller_env, monkeypatch):
    live, archive = poller_env
    today = datetime.now(pln.IST).strftime("%Y-%m-%d")
    monkeypatch.setattr(pln, "fetch_today", lambda now: [
        nse_record(11, f"{today} 10:00:00"), nse_record(12, f"{today} 16:00:00"),
        nse_record(9, "2020-01-01 10:00:00"),          # before today: dropped
    ])
    assert pln.main() == 0
    payload = json.loads(live.read_text())
    assert payload["trading_day"] == today and payload["n_new_this_poll"] == 2
    assert [i["seq_id"] for i in payload["items"]] == [12, 11]
    assert [i["seq_id"] for i in na.load_day(archive, today)] == [12, 11]
    idx = json.loads((archive / "index.json").read_text())
    assert idx["n_total"] == 2
    assert idx["days"][0] == {"day": today, "n": 2, "n_after": 1, "file": f"{today}.json"}

    # a second poll with nothing new exits 1 and leaves the archive byte-identical
    before = {p.name: p.read_text() for p in archive.iterdir()}
    assert pln.main() == 1
    assert {p.name: p.read_text() for p in archive.iterdir()} == before


def test_main_exits_2_and_writes_nothing_when_todays_shard_is_corrupt(poller_env, monkeypatch):
    live, archive = poller_env
    today = datetime.now(pln.IST).strftime("%Y-%m-%d")
    archive.mkdir()
    shard = archive / f"{today}.json"
    shard.write_text(CONFLICTED)
    monkeypatch.setattr(pln, "fetch_today", lambda now: pytest.fail("must not fetch when a feed file is corrupt"))
    assert pln.main() == 2
    assert not live.exists() and shard.read_text() == CONFLICTED and not (archive / "index.json").exists()


def test_main_exits_2_when_the_live_file_is_corrupt(poller_env, monkeypatch):
    live, archive = poller_env
    live.write_text(CONFLICTED)
    monkeypatch.setattr(pln, "fetch_today", lambda now: pytest.fail("must not fetch when a feed file is corrupt"))
    assert pln.main() == 2
    assert live.read_text() == CONFLICTED and not archive.exists()
