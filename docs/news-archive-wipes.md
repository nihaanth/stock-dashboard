# The news archive wipes (June to August 2026) and what now prevents them

`frontdesign/data/_news_history.json` is meant to keep every NSE announcement
ever polled. It was wiped seven times. Each time the whole archive was replaced
by a file holding only the items polled in the previous few minutes.

| Wipe (IST) | Archive before | Archive after |
|---|---|---|
| 11 Jun 10:52 | 40,203 items, 116 days (back to 12 Feb) | 15 items |
| 15 Jun 11:46 | 5,502 items | 44 items |
| 18 Jun 11:01 | 4,968 items | 32 items |
| 19 Jun 11:37 | 4,160 items | 37 items |
| 26 Jun 10:34 | 6,440 items | 16 items |
| 29 Jun 10:54 | 5,422 items | 19 items |
| 4 Aug 09:30 | 13,936 items | 9 items |

## Mechanism

1. The gap-backfill workflow is scheduled outside the polling window, but
   GitHub delivered its 01:00 UTC cron hours late, inside the window. It pushed
   a commit that rewrote both feed files.
2. The running poller link had a checkout from before that push. Its
   `git pull --rebase` conflicted on both files (they both change `updated_at`,
   `n_total` and the head of `items[]`). The loop ignored the failure with
   `|| true`, and its `git push origin HEAD:main` was a silent no-op because the
   half-finished rebase had left HEAD on the remote tip.
3. On the next cycle the poller read files full of conflict markers, caught the
   JSON error, started from an empty list, rewrote the archive with a few
   minutes of items, committed on the detached HEAD and pushed. That push was a
   fast-forward, so it landed.

Every wipe commit in git is the direct child of a backfill commit, and its
`_live_news.json` reports every item as new, which only happens when the
poller could not parse its own previous file.

## What changed

- `automation/live_poll_loop.sh` resets the tree to the current tip of main
  before every poll and never rebases. A rejected push means "reset to main and
  re-run the poll", which is safe because the merge is idempotent.
- `.github/workflows/news_backfill.yml` does the same: fetch, reset, re-run.
- `scripts/poll_live_news.py` and `scripts/backfill_news_history.py` refuse to
  touch a feed file that exists but does not parse (exit 2). A corrupt file now
  costs one cycle, not the archive.
- `scripts/guard_news_files.py` runs before every commit in both writers and
  refuses an archive that does not parse or that is smaller than the copy in
  HEAD.
- `tests/test_live_poll_loop.py` replays the race against a local bare repo.

## Recovering the data

Nothing was lost from git. The last copy before each wipe is still there, so the
full archive is the union of those copies:

```bash
python scripts/recover_news_history.py --dry-run   # report
python scripts/recover_news_history.py             # rewrite the archive
```
