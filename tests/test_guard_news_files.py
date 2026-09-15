import json
import os
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import guard_news_files as guard
import news_archive as na

NOW = datetime(2026, 6, 11, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env={**os.environ, **GIT_ENV})


def item(seq, day, t="10:00:00"):
    return {"seq_id": seq, "symbol": f"S{seq}", "sort_date": f"{day} {t}", "desc": "d"}


@pytest.fixture
def repo(tmp_path):
    git("init", "-q", "-b", "main", str(tmp_path), cwd=tmp_path)
    archive = tmp_path / guard.ARCHIVE
    na.update_archive([item(1, "2026-06-10"), item(2, "2026-06-10", "16:00:00"), item(3, "2026-06-11")], NOW, archive)
    live = tmp_path / guard.LIVE
    live.write_text(json.dumps({"items": [item(3, "2026-06-11")]}))
    git("add", "-A", cwd=tmp_path)
    git("commit", "-q", "-m", "seed", cwd=tmp_path)
    return tmp_path, archive, live


def run(root):
    return guard.main(["--root", str(root)])


def test_no_changes_is_ok(repo):
    assert run(repo[0]) == 0


def test_growing_a_shard_through_the_writer_is_ok(repo):
    root, archive, _ = repo
    na.update_archive([item(4, "2026-06-11")], NOW, archive)
    assert run(root) == 0


def test_a_new_day_is_ok(repo):
    root, archive, _ = repo
    na.update_archive([item(5, "2026-06-12")], NOW, archive)
    assert run(root) == 0


def test_a_shrunken_shard_is_refused_even_with_a_consistent_index(repo):
    root, archive, _ = repo
    na.write_json(na.shard_path(archive, "2026-06-10"), na.day_payload("2026-06-10", [item(1, "2026-06-10")]))
    na.rebuild_index(archive, NOW)
    assert run(root) == 1


def test_a_deleted_shard_is_refused(repo):
    root, archive, _ = repo
    na.shard_path(archive, "2026-06-10").unlink()
    na.rebuild_index(archive, NOW)
    assert run(root) == 1


def test_a_changed_shard_without_an_index_update_is_refused(repo):
    root, archive, _ = repo
    na.write_json(na.shard_path(archive, "2026-06-11"),
                  na.day_payload("2026-06-11", [item(4, "2026-06-11", "11:00:00"), item(3, "2026-06-11")]))
    assert run(root) == 1


def test_an_untracked_shard_the_index_does_not_know_is_refused(repo):
    root, archive, _ = repo
    na.write_json(na.shard_path(archive, "2026-06-12"), na.day_payload("2026-06-12", [item(7, "2026-06-12")]))
    assert run(root) == 1


def test_an_index_that_drops_a_committed_day_is_refused(repo):
    root, archive, _ = repo
    idx = json.loads((archive / "index.json").read_text())
    idx["days"] = [d for d in idx["days"] if d["day"] != "2026-06-10"]
    idx["n_total"] = 1
    (archive / "index.json").write_text(json.dumps(idx))
    assert run(root) == 1


def test_an_index_whose_total_does_not_add_up_is_refused(repo):
    root, archive, _ = repo
    idx = json.loads((archive / "index.json").read_text())
    idx["n_total"] = 99
    (archive / "index.json").write_text(json.dumps(idx))
    assert run(root) == 1


def test_corrupt_files_are_refused(repo):
    root, archive, live = repo
    live.write_text("<<<<<<< HEAD")
    assert run(root) == 1
    git("checkout", "--", guard.LIVE, cwd=root)
    (archive / "index.json").write_text("{")
    assert run(root) == 1


def test_a_valid_live_feed_change_is_ok(repo):
    root, _, live = repo
    live.write_text(json.dumps({"items": []}))
    assert run(root) == 0


def test_check_shard_messages():
    ok, msg, n = guard.check_shard("x.json", json.dumps({"items": [1, 2]}), json.dumps({"items": [1, 2, 3]}))
    assert not ok and "shrink 3 -> 2" in msg and n == 2
    ok, msg, n = guard.check_shard("x.json", json.dumps({"items": [1]}), None)
    assert ok and "new day" in msg
    ok, msg, n = guard.check_shard("x.json", "<<<<<<<", None)
    assert not ok and n == -1
