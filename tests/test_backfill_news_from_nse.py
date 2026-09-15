import json

import pytest

import backfill_news_from_nse as bf
import news_archive as na


def rec(seq, when, sym="TESTCO"):
    return {"symbol": sym, "seq_id": seq, "sort_date": when, "an_dt": None,
            "desc": "Outcome of Board Meeting", "sm_name": "Test Co", "attchmntFile": None}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(bf, "load_stock_map", lambda: {"TESTCO": {"industry_slug": "banks", "name": "Test Co"}})
    snap = tmp_path / "nse.json"
    snap.write_text(json.dumps([
        rec(1, "2026-06-10 10:00:00"), rec(2, "2026-06-10 16:00:00"), rec(3, "2026-06-11 09:30:00"),
        rec(4, "2026-06-11 09:31:00", sym="NOT-IN-UNIVERSE"),
    ]))
    archive, live = tmp_path / "news", tmp_path / "live.json"
    monkeypatch.setattr("sys.argv", ["backfill", "--snapshot", str(snap),
                                     "--archive-dir", str(archive), "--live-out", str(live)])
    return archive, live


def test_snapshot_backfill_fills_shards_and_derives_the_live_feed(env):
    archive, live = env
    assert bf.main() == 0
    assert [i["seq_id"] for i in na.load_day(archive, "2026-06-10")] == [2, 1]
    idx = json.loads((archive / "index.json").read_text())
    assert idx["n_total"] == 3 and idx["days"][0]["day"] == "2026-06-11"
    lp = json.loads(live.read_text())
    assert lp["trading_day"] == "2026-06-11" and lp["n_new_this_poll"] == 0
    assert [i["seq_id"] for i in lp["items"]] == [3]
    # idempotent: a second run adds nothing and reports it via the exit code
    assert bf.main() == 1


def test_a_corrupt_shard_is_fatal_and_left_untouched(env):
    archive, live = env
    archive.mkdir()
    (archive / "2026-06-10.json").write_text("<<<<<<< HEAD\n{")
    with pytest.raises(na.CorruptFeedError):
        bf.main()
    assert (archive / "2026-06-10.json").read_text() == "<<<<<<< HEAD\n{"
    assert not live.exists() and not (archive / "2026-06-11.json").exists()
