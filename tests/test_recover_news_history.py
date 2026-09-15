import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("recover_news_history", ROOT / "scripts" / "recover_news_history.py")
rec = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rec)


def it(seq, sym="A", sort_date="2026-06-11 10:00:00", **extra):
    return {"seq_id": seq, "symbol": sym, "sort_date": sort_date, **extra}


def test_select_versions_keeps_newest_and_every_pre_shrink_copy():
    # newest first: 40 -> wiped to 1 -> regrew to 5 -> wiped to 2 (current)
    sizes = [2, 5, 1, 40]
    assert rec.select_versions(sizes) == [0, 1, 3]
    assert rec.select_versions([10, 9, 8]) == [0]     # monotonic growth: only the newest
    assert rec.select_versions([]) == []


def test_union_dedups_by_seq_id_and_newest_copy_wins():
    newest = [it(2, industry_slug="new-slug")]
    older = [it(2, industry_slug="old-slug"), it(1, sort_date="2026-06-10 09:00:00")]
    merged = rec.union_versions([newest, older])
    assert [m["seq_id"] for m in merged] == [2, 1]
    assert merged[0]["industry_slug"] == "new-slug"


def test_union_drops_synthetic_ids_shadowed_by_a_real_record():
    real = [it(100, sym="X", sort_date="2026-05-01 12:00:00")]
    synthetic = [it(-7, sym="X", sort_date="2026-05-01 12:00:00"),
                 it(-8, sym="Y", sort_date="2026-05-01 12:00:00")]   # no real twin: kept
    merged = rec.union_versions([real, synthetic])
    assert sorted(m["seq_id"] for m in merged) == [-8, 100]


def test_union_ignores_records_without_a_usable_seq_id():
    merged = rec.union_versions([[{"symbol": "A"}, it("12"), it(None)]])
    assert [m["seq_id"] for m in merged] == [12]


def test_build_payload_lists_days_newest_first():
    p = rec.build_payload([it(3, sort_date="2026-06-11 10:00:00"), it(2, sort_date="2026-06-11 09:00:00"),
                           it(1, sort_date="2026-06-10 09:00:00")], rec.datetime(2026, 6, 12, tzinfo=rec.IST))
    assert p["trading_days"] == ["2026-06-11", "2026-06-10"] and p["n_total"] == 3
