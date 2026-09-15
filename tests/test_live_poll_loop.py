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

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOOP = ROOT / "automation" / "live_poll_loop.sh"
HIST = "frontdesign/data/_news_history.json"
LIVE = "frontdesign/data/_live_news.json"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV}).stdout


def payload(seqs):
    items = [{"seq_id": s, "symbol": f"S{s}", "industry_slug": "banks", "name": "x",
              "sort_date": f"2026-06-11 10:{s % 60:02d}:00", "desc": "d"} for s in seqs]
    return json.dumps({"updated_at": "seed", "polled_at": "seed", "trading_day": "2026-06-11",
                       "market_state": "open", "n_new_this_poll": 0, "trading_days": ["2026-06-11"],
                       "n_total": len(items), "items": items}, indent=2)


def items_on_main(origin, rel):
    text = subprocess.run(["git", "--git-dir", str(origin), "show", f"main:{rel}"],
                          check=True, capture_output=True, text=True).stdout
    return [i["seq_id"] for i in json.loads(text)["items"]]


# A stand-in for poll_live_news.py: prepends one item (seq 9002) to both files.
# On its FIRST call it also pushes a competing commit (seq 9001) to origin from a
# second clone -- the backfill landing between our poll and our push.
FAKE_POLL = textwrap.dedent('''
    import json, os, subprocess, sys
    HIST, LIVE = %r, %r
    def add(base, seq, stamp):
        for rel in (HIST, LIVE):
            p = os.path.join(base, rel)
            d = json.load(open(p))
            d["items"].insert(0, {"seq_id": seq, "symbol": f"S{seq}", "industry_slug": "banks",
                                  "name": "x", "sort_date": f"2026-06-11 11:{seq %% 60:02d}:00", "desc": "d"})
            d["n_total"] = len(d["items"]); d["updated_at"] = d["polled_at"] = stamp
            json.dump(d, open(p, "w"), indent=2)
    marker, other = os.environ["MARKER"], os.environ["OTHER"]
    if os.environ.get("RACE") == "1" and not os.path.exists(marker):
        open(marker, "w").close()
        add(other, 9001, "backfill")
        subprocess.run(["git", "commit", "-qam", "backfill"], cwd=other, check=True)
        subprocess.run(["git", "push", "-q", "origin", "HEAD:main"], cwd=other, check=True)
    if os.environ.get("WIPE") == "1":
        for rel in (HIST, LIVE):
            json.dump({"items": [{"seq_id": 1, "symbol": "S1", "sort_date": "2026-06-11 10:01:00"}]},
                      open(rel, "w"))
        sys.exit(0)
    add(".", 9002, "poll")
    sys.exit(0)
''') % (HIST, LIVE)


@pytest.fixture
def repos(tmp_path):
    origin = tmp_path / "origin.git"
    git("init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    seed = tmp_path / "seed"
    git("clone", "-q", str(origin), str(seed), cwd=tmp_path)
    (seed / HIST).parent.mkdir(parents=True)
    (seed / HIST).write_text(payload([3, 2, 1]))
    (seed / LIVE).write_text(payload([3]))
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
    assert items_on_main(origin, HIST) == [9002, 9001, 3, 2, 1]
    assert items_on_main(origin, LIVE) == [9002, 9001, 3]
    assert "<<<<<<<" not in subprocess.run(["git", "--git-dir", str(origin), "show", f"main:{HIST}"],
                                         check=True, capture_output=True, text=True).stdout
    assert not (work / ".git" / "rebase-merge").exists()


def test_plain_cycle_pushes_a_fast_forward(repos, tmp_path):
    origin, work, other, fake = repos
    log = run_loop(work, other, fake, tmp_path)
    assert "pushed (attempt 1)" in log, log
    assert items_on_main(origin, HIST) == [9002, 3, 2, 1]


def test_guard_blocks_a_shrunken_archive_and_resyncs(repos, tmp_path):
    origin, work, other, fake = repos
    log = run_loop(work, other, fake, tmp_path, WIPE="1")
    assert "would shrink 3 -> 1" in log and "guard refused" in log, log
    assert items_on_main(origin, HIST) == [3, 2, 1]                       # main untouched
    assert json.loads((work / HIST).read_text())["n_total"] == 3           # tree reset to main


def test_poll_starts_from_the_current_tip_of_main(repos, tmp_path):
    origin, work, other, fake = repos
    # Leave conflict markers in the checkout, and let main move ahead: the loop
    # must reset to main BEFORE polling, so the poll sees clean, current files.
    (work / HIST).write_text("<<<<<<< HEAD\n{ garbage")
    (other / HIST).write_text(payload([4, 3, 2, 1]))
    git("commit", "-qam", "someone else", cwd=other)
    git("push", "-q", "origin", "HEAD:main", cwd=other)
    log = run_loop(work, other, fake, tmp_path)
    assert "pushed (attempt 1)" in log, log
    assert items_on_main(origin, HIST) == [9002, 4, 3, 2, 1]
