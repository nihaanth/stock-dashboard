"""End-to-end tests for automation/live_poll_loop.sh against a local bare 'origin'.

They replay the race that wiped the archive: another writer (the backfill) pushes
to main between the poller's poll and its push. The loop must end with BOTH
writers' items on main, no conflict markers, and never a smaller archive.
"""
import json
import os
import pathlib
import subprocess
import sys
import textwrap
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import news_archive as na

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOOP = ROOT / "automation" / "live_poll_loop.sh"
LIVE = "frontdesign/data/_live_news.json"
NEWS = "frontdesign/data/news"
SHARD = f"{NEWS}/2026-06-11.json"
NOW = datetime(2026, 6, 11, 10, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV}).stdout


def item(seq):
    return {"seq_id": seq, "symbol": f"S{seq}", "industry_slug": "banks", "name": "x",
            "sort_date": f"2026-06-11 10:{seq % 60:02d}:00", "desc": "d"}


def live_payload(seqs):
    items = [item(s) for s in seqs]
    return json.dumps({"polled_at": "seed", "trading_day": "2026-06-11", "market_state": "open",
                       "n_new_this_poll": 0, "n_total": len(items), "items": items}, indent=2)


def items_on_main(origin, rel):
    text = subprocess.run(["git", "--git-dir", str(origin), "show", f"main:{rel}"],
                          check=True, capture_output=True, text=True).stdout
    return [i["seq_id"] for i in json.loads(text)["items"]]


# A stand-in for poll_live_news.py: merges one item (seq 9002) into today's shard
# through the real archive writer and prepends it to the live feed. On its FIRST
# call with RACE=1 it also pushes a competing commit (seq 9001) to origin from a
# second clone -- the backfill landing between our poll and our push.
FAKE_POLL = textwrap.dedent('''
    import json, os, subprocess, sys
    from datetime import datetime
    from pathlib import Path
    from zoneinfo import ZoneInfo
    sys.path.insert(0, %r)
    import news_archive as na
    LIVE, NEWS = %r, %r
    NOW = datetime(2026, 6, 11, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    def item(seq):
        return {"seq_id": seq, "symbol": f"S{seq}", "industry_slug": "banks", "name": "x",
                "sort_date": f"2026-06-11 11:{seq %% 60:02d}:00", "desc": "d"}
    def add(base, seq, stamp):
        base = Path(base)
        na.update_archive([item(seq)], NOW, base / NEWS)
        p = base / LIVE
        d = json.loads(p.read_text())
        d["items"].insert(0, item(seq)); d["n_total"] = len(d["items"]); d["polled_at"] = stamp
        p.write_text(json.dumps(d, indent=2))
    marker, other = os.environ["MARKER"], os.environ["OTHER"]
    if os.environ.get("RACE") == "1" and not os.path.exists(marker):
        open(marker, "w").close()
        add(other, 9001, "backfill")
        subprocess.run(["git", "add", "-A"], cwd=other, check=True)
        subprocess.run(["git", "commit", "-qm", "backfill"], cwd=other, check=True)
        subprocess.run(["git", "push", "-q", "origin", "HEAD:main"], cwd=other, check=True)
    if os.environ.get("WIPE") == "1":
        na.write_json(Path(NEWS) / "2026-06-11.json", na.day_payload("2026-06-11", [item(1)]))
        na.rebuild_index(Path(NEWS), NOW)
        sys.exit(0)
    add(".", 9002, "poll")
    sys.exit(0)
''') % (str(ROOT / "scripts"), LIVE, NEWS)


@pytest.fixture
def repos(tmp_path):
    origin = tmp_path / "origin.git"
    git("init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    seed = tmp_path / "seed"
    git("clone", "-q", str(origin), str(seed), cwd=tmp_path)
    na.update_archive([item(1), item(2), item(3)], NOW, seed / NEWS)
    (seed / LIVE).write_text(live_payload([3]))
    git("add", "-A", cwd=seed)
    git("commit", "-q", "-m", "seed", cwd=seed)
    git("push", "-q", "origin", "HEAD:main", cwd=seed)
    work, other = tmp_path / "work", tmp_path / "other"
    git("clone", "-q", str(origin), str(work), cwd=tmp_path)
    git("clone", "-q", str(origin), str(other), cwd=tmp_path)
    fake = tmp_path / "fake_poll.py"
    fake.write_text(FAKE_POLL)
    return origin, work, other, fake


def run_loop(work, other, fake, tmp_path, **flags):
    env = {**os.environ, **GIT_ENV,
           "ROOT": str(work), "PY": sys.executable, "NO_SYNC": "0",
           "FORCE_PLAN": "poll 1", "MAX_ITERS": "1",
           "POLL_CMD": f"{sys.executable} {fake}",
           "MARKER": str(tmp_path / "marker"), "OTHER": str(other), **flags}
    r = subprocess.run(["bash", str(LOOP)], cwd=work, env=env, capture_output=True, text=True, timeout=120)
    return r.stdout + r.stderr


def test_concurrent_push_is_merged_not_rebased(repos, tmp_path):
    origin, work, other, fake = repos
    log = run_loop(work, other, fake, tmp_path, RACE="1")
    assert "push rejected (attempt 1)" in log and "pushed (attempt 2)" in log, log
    assert items_on_main(origin, SHARD) == [9002, 9001, 3, 2, 1]
    assert items_on_main(origin, LIVE) == [9002, 9001, 3]
    idx = json.loads(subprocess.run(["git", "--git-dir", str(origin), "show", f"main:{NEWS}/index.json"],
                                    check=True, capture_output=True, text=True).stdout)
    assert idx["n_total"] == 5
    assert not (work / ".git" / "rebase-merge").exists()


def test_plain_cycle_pushes_a_fast_forward(repos, tmp_path):
    origin, work, other, fake = repos
    log = run_loop(work, other, fake, tmp_path)
    assert "pushed (attempt 1)" in log, log
    assert items_on_main(origin, SHARD) == [9002, 3, 2, 1]


def test_guard_blocks_a_shrunken_archive_and_resyncs(repos, tmp_path):
    origin, work, other, fake = repos
    log = run_loop(work, other, fake, tmp_path, WIPE="1")
    assert "would shrink 3 -> 1" in log and "guard refused" in log, log
    assert items_on_main(origin, SHARD) == [3, 2, 1]                       # main untouched
    assert json.loads((work / SHARD).read_text())["n_total"] == 3          # tree reset to main
    assert git("status", "--porcelain", cwd=work) == ""


def test_poll_starts_from_the_current_tip_of_main(repos, tmp_path):
    origin, work, other, fake = repos
    # Leave conflict markers in the checkout, and let main move ahead: the loop
    # must reset to main BEFORE polling, so the poll sees clean, current files.
    (work / SHARD).write_text("<<<<<<< HEAD\n{ garbage")
    na.update_archive([item(4)], NOW, other / NEWS)
    git("commit", "-qam", "someone else", cwd=other)
    git("push", "-q", "origin", "HEAD:main", cwd=other)
    log = run_loop(work, other, fake, tmp_path)
    assert "pushed (attempt 1)" in log, log
    assert items_on_main(origin, SHARD) == [9002, 4, 3, 2, 1]


def test_new_day_shard_is_staged_and_pushed(repos, tmp_path):
    origin, work, other, fake = repos
    fake.write_text(FAKE_POLL.replace('"sort_date": f"2026-06-11 11:', '"sort_date": f"2026-06-12 11:'))
    log = run_loop(work, other, fake, tmp_path)
    assert "pushed (attempt 1)" in log, log
    assert items_on_main(origin, f"{NEWS}/2026-06-12.json") == [9002]      # untracked new file made it
