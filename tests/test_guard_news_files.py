import importlib.util
import json
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("guard_news_files", ROOT / "scripts" / "guard_news_files.py")
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def feed(n):
    return json.dumps({"items": [{"seq_id": i} for i in range(n)]})


def test_archive_may_grow_or_stay_equal():
    assert guard.evaluate(feed(5), feed(3), "h.json", append_only=True)[0]
    assert guard.evaluate(feed(3), feed(3), "h.json", append_only=True)[0]


def test_archive_may_not_shrink():
    ok, msg = guard.evaluate(feed(2), feed(3), "h.json", append_only=True)
    assert not ok and "shrink 3 -> 2" in msg


def test_unparseable_file_is_refused_even_without_a_baseline():
    ok, msg = guard.evaluate("<<<<<<< HEAD\n{", None, "h.json", append_only=True)
    assert not ok and "not valid JSON" in msg
    assert not guard.evaluate('{"updated_at": 1}', None, "h.json", append_only=False)[0]


def test_first_commit_and_unreadable_baseline_are_accepted():
    assert guard.evaluate(feed(1), None, "h.json", append_only=True)[0]
    assert guard.evaluate(feed(1), "garbage", "h.json", append_only=True)[0]


def test_live_file_may_shrink():
    assert guard.evaluate(feed(1), feed(900), "live.json", append_only=False)[0]


def test_cli_compares_against_git_head(tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    rel = "frontdesign/data/_news_history.json"
    (tmp_path / rel).parent.mkdir(parents=True)
    (tmp_path / rel).write_text(feed(3))
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=tmp_path, check=True, env={**os.environ, **env})
    (tmp_path / rel).write_text(feed(4))
    assert guard.main(["--root", str(tmp_path), rel]) == 0
    (tmp_path / rel).write_text(feed(2))
    assert guard.main(["--root", str(tmp_path), rel]) == 1
    (tmp_path / rel).write_text("<<<<<<< HEAD")
    assert guard.main(["--root", str(tmp_path), rel]) == 1
