"""The shared archive writer: append-only, deduplicated, and never overwrites a
file it could not parse (which is how the previous single-file archive was wiped)."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import news_archive as na

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 6, 11, 10, 52, tzinfo=IST)


def item(seq, day="2026-06-11", t="10:51:00", sym="TESTCO"):
    return {"seq_id": seq, "symbol": sym, "industry_slug": "banks", "name": "Test Co",
            "sort_date": f"{day} {t}", "an_dt": None, "desc": "General Updates",
            "sm_name": "Test Co", "attchmntFile": None}


def snapshot(d):
    return {p.name: p.read_text() for p in d.iterdir()}


def test_update_archive_creates_shards_and_index(tmp_path):
    upd = na.update_archive([item(1), item(2, day="2026-06-10", t="16:00:00")], NOW, tmp_path)
    assert (upd.n_added, upd.n_total) == (2, 2)
    assert upd.days == ["2026-06-11", "2026-06-10"] and upd.touched == ["2026-06-10", "2026-06-11"]
    idx = json.loads((tmp_path / "index.json").read_text())
    assert idx["n_total"] == 2 and [d["day"] for d in idx["days"]] == ["2026-06-11", "2026-06-10"]
    assert idx["days"][1] == {"day": "2026-06-10", "n": 1, "n_after": 1, "file": "2026-06-10.json"}
    shard = json.loads((tmp_path / "2026-06-11.json").read_text())
    assert shard["day"] == "2026-06-11" and shard["n_total"] == 1 and shard["n_after_market"] == 0
    assert shard["items"][0]["seq_id"] == 1


def test_update_archive_appends_dedups_and_sorts_newest_first(tmp_path):
    na.update_archive([item(1, t="09:00:00")], NOW, tmp_path)
    upd = na.update_archive([item(1, t="09:00:00"), item(2, t="10:00:00")], NOW, tmp_path)
    assert upd.n_added == 1
    assert [i["seq_id"] for i in na.load_day(tmp_path, "2026-06-11")] == [2, 1]


def test_update_archive_is_a_noop_without_new_items(tmp_path):
    na.update_archive([item(1)], NOW, tmp_path)
    before = snapshot(tmp_path)
    upd = na.update_archive([item(1)], datetime(2026, 6, 12, tzinfo=IST), tmp_path)
    assert upd.n_added == 0 and upd.touched == []
    assert snapshot(tmp_path) == before   # no timestamp churn either


def test_update_archive_refuses_to_touch_a_corrupt_shard_and_writes_nothing(tmp_path):
    na.update_archive([item(1), item(9, day="2026-06-10")], NOW, tmp_path)
    (tmp_path / "2026-06-11.json").write_text("<<<<<<< HEAD\n{")
    before = snapshot(tmp_path)
    with pytest.raises(na.CorruptFeedError):
        na.update_archive([item(2), item(8, day="2026-06-10")], NOW, tmp_path)
    assert snapshot(tmp_path) == before   # not even the healthy shard was written


def test_update_archive_refuses_a_corrupt_index(tmp_path):
    (tmp_path / "index.json").write_text("{not json")
    with pytest.raises(na.CorruptFeedError):
        na.update_archive([item(1)], NOW, tmp_path)
    assert not (tmp_path / "2026-06-11.json").exists()


def test_update_archive_rebuilds_a_missing_index_from_the_shards(tmp_path):
    na.update_archive([item(1)], NOW, tmp_path)
    (tmp_path / "index.json").unlink()
    upd = na.update_archive([item(5, day="2026-06-12")], NOW, tmp_path)
    assert upd.n_total == 2 and upd.days == ["2026-06-12", "2026-06-11"]


def test_update_archive_repairs_stale_index_counts(tmp_path):
    na.update_archive([item(1)], NOW, tmp_path)
    idx = json.loads((tmp_path / "index.json").read_text())
    idx["days"][0]["n"] = 0
    idx["n_total"] = 0
    (tmp_path / "index.json").write_text(json.dumps(idx))
    upd = na.update_archive([item(1)], NOW, tmp_path)   # nothing new, but the index disagrees
    assert upd.n_added == 0
    assert json.loads((tmp_path / "index.json").read_text())["n_total"] == 1


def test_items_without_a_seq_id_or_a_day_are_ignored(tmp_path):
    upd = na.update_archive([{"symbol": "X", "sort_date": "2026-06-11 10:00:00"},
                             {"seq_id": 3, "symbol": "X"}, item(4)], NOW, tmp_path)
    assert (upd.n_added, upd.n_total) == (1, 1)


def test_rebuild_index_and_iter_all_items(tmp_path):
    na.update_archive([item(1), item(2, day="2026-06-10")], NOW, tmp_path)
    (tmp_path / "index.json").unlink()
    idx = na.rebuild_index(tmp_path, NOW)
    assert idx["n_total"] == 2
    assert [i["seq_id"] for i in na.iter_all_items(tmp_path)] == [1, 2]


def test_is_after_market_boundary():
    assert na.is_after_market({"sort_date": "2026-06-11 15:30:00"})
    assert not na.is_after_market({"sort_date": "2026-06-11 15:29:59"})
    assert not na.is_after_market({"sort_date": "2026-06-11"})


def test_merge_items_existing_copy_wins_and_seq_ids_are_normalised():
    merged, added = na.merge_items(
        [{"seq_id": 1, "sort_date": "a", "v": "old"}],
        [{"seq_id": "1", "sort_date": "a", "v": "new"}, {"seq_id": "2", "sort_date": "b"}])
    assert added == 1
    assert [m["seq_id"] for m in merged] == [2, 1] and merged[1]["v"] == "old"


def test_cli_stats_and_rebuild(tmp_path, capsys):
    na.update_archive([item(1)], NOW, tmp_path)
    assert na.main(["--archive-dir", str(tmp_path), "--rebuild-index", "--stats"]) == 0
    out = capsys.readouterr().out
    assert "index rebuilt: 1 items over 1 days" in out and "1 shard files" in out
