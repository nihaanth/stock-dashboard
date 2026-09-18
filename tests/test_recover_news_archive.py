import json
import os
import subprocess

import news_archive as na
import recover_news_archive as rec

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def it(seq, sym="A", sort_date="2026-06-11 10:00:00", **extra):
    return {"seq_id": seq, "symbol": sym, "sort_date": sort_date, **extra}


def test_select_versions_keeps_newest_and_every_pre_shrink_copy():
    # newest first: 40 -> wiped to 1 -> regrew to 5 -> wiped to 2 (current)
    assert rec.select_versions([2, 5, 1, 40]) == [0, 1, 3]
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


def test_end_to_end_rebuild_from_git_history(tmp_path):
    repo = tmp_path
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    legacy = repo / rec.LEGACY_REL
    legacy.parent.mkdir(parents=True)

    def commit(items, msg):
        legacy.write_text(json.dumps({"n_total": len(items), "items": items}))
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True, env={**os.environ, **GIT_ENV})

    a, b = it(1, sort_date="2026-06-10 10:00:00"), it(2, sort_date="2026-06-10 11:00:00")
    c, d, e = it(3, sort_date="2026-06-11 09:00:00"), it(4, sort_date="2026-06-11 10:00:00"), it(5, sort_date="2026-06-12 10:00:00")
    commit([a, b], "grow")
    commit([c, b, a], "grow more")      # the last full copy before the wipe
    commit([d], "wipe")                 # archive collapsed to one item
    commit([e, d], "regrow")            # current committed state

    assert rec.main(["--repo", str(repo), "--ref", "HEAD"]) == 0
    archive = repo / "frontdesign" / "data" / "news"
    idx = json.loads((archive / "index.json").read_text())
    assert idx["n_total"] == 5
    assert [x["day"] for x in idx["days"]] == ["2026-06-12", "2026-06-11", "2026-06-10"]
    assert [i["seq_id"] for i in na.load_day(archive, "2026-06-11")] == [4, 3]
    assert [i["seq_id"] for i in na.load_day(archive, "2026-06-10")] == [2, 1]

    # re-running merges nothing new and never shrinks anything
    assert rec.main(["--repo", str(repo), "--ref", "HEAD"]) == 0
    assert json.loads((archive / "index.json").read_text())["n_total"] == 5
