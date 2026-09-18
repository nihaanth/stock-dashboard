# The news archive

## Layout

Every NSE corporate announcement the poller has seen for the eligible universe
lives under `frontdesign/data/news/`, one file per calendar day:

```
frontdesign/data/news/index.json        {updated_at, n_total, days: [{day, n, n_after, file}, ...]}
frontdesign/data/news/2026-09-02.json   {day, n_total, n_after_market, items: [...]}
frontdesign/data/_live_news.json        today's feed (the News tab and the ticker)
```

`index.json` has one small row per day, newest first. `n_after` counts filings
at or after 15:30 IST, so the After-Market page can draw its day strip without
opening a single shard. A shard holds that day's items, newest first, in the
same shape as the live feed.

All writers go through `scripts/news_archive.py`:

- `scripts/poll_live_news.py` merges today's items into today's shard.
- `scripts/backfill_news_from_nse.py` merges a date range from the NSE API.
- `scripts/backfill_news_history.py` seeds from the per-stock JSON files.
- `scripts/recover_news_archive.py` rebuilds the archive from git history.

The front-end (`frontdesign/industries.js`) polls the index every 30 seconds
together with the live feed, and fetches a day's shard only when a page shows
that day. Shard URLs carry `?v=<count>`, so unchanged days stay cached while a
day that grew is re-fetched.

Invariants: items are deduplicated by `seq_id`; shards only grow; a file that
exists but does not parse is never overwritten. `scripts/guard_news_files.py`
enforces the last two before every commit.

## Why it is built this way: the 2026 wipes

The archive used to be one file, `frontdesign/data/_news_history.json`, meant
to keep everything forever. It was wiped seven times. Each time the whole
archive was replaced by a file holding only the items polled in the previous
few minutes.

| Wipe (IST) | Archive before | Archive after |
|---|---|---|
| 11 Jun 10:52 | 40,203 items, 116 days (back to 12 Feb) | 15 items |
| 15 Jun 11:46 | 5,502 items | 44 items |
| 18 Jun 11:01 | 4,968 items | 32 items |
| 19 Jun 11:37 | 4,160 items | 37 items |
| 26 Jun 10:34 | 6,440 items | 16 items |
| 29 Jun 10:54 | 5,422 items | 19 items |
| 4 Aug 09:30 | 13,936 items | 9 items |

### Mechanism

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

### What changed

- `automation/live_poll_loop.sh` resets the tree to the current tip of main
  before every poll and never rebases. A rejected push means "reset to main and
  re-run the poll", which is safe because the merge is idempotent.
- `.github/workflows/news_backfill.yml` does the same: fetch, reset, re-run.
- Every writer refuses to touch a feed file that exists but does not parse
  (exit 2). A corrupt file now costs one cycle, not the archive.
- `scripts/guard_news_files.py` runs before every commit in both writers and
  refuses a shard or index that does not parse, that shrinks, or that loses a
  day.
- The archive is sharded by day, so a poll rewrites a few hundred kilobytes
  instead of the whole history, and the site never downloads more than the day
  it shows.
- `tests/test_live_poll_loop.py` replays the race against a local bare repo.

## Recovering data

Nothing was lost from git. The last copy before each wipe is still there, and
`scripts/recover_news_archive.py` merges every committed version of the old
single file, plus the current shards, into the archive:

```bash
python scripts/recover_news_archive.py --dry-run   # report what would be restored
python scripts/recover_news_archive.py             # merge into the shards, rebuild the index
```

It never shrinks a shard, so it is safe to re-run at any time.

Migration note: `frontdesign/data/_news_history.json` is kept on purpose. No
code in this repository reads or writes it any more, and the front-end never
fetches it, but it stays in the tree as a historical record of the old layout
and as the source the recovery script reads when it rebuilds shards from git
history. It is deliberately not deleted. Whatever the last old poller link
writes into it is folded into the shards by running the recovery once; the
file itself simply stays. Two true gaps
remain, both from weeks when the poller was down and the next backfill only
reached 14 days back: 3 to 17 July 2026 and 5 to 17 August 2026. A manual
`python scripts/backfill_news_from_nse.py --from 03-07-2026 --to 17-07-2026`
(and the same for August) would close them if the NSE API still serves those
ranges.

## How often the poller may commit

Every poll that finds a new announcement becomes a commit on `main`, and every
push to `main` is one Vercel deployment. Vercel's free plan allows 100 a day.

The poller originally ran at 180s during the session and 600s after it, which
works out at up to 182 polls a day. On 17 and 18 September 2026 it pushed 105
and 101 times, so the project hit the cap each afternoon and Vercel refused to
build anything, with:

```
Resource is limited - try again in 24 hours (more than 100, code: "api-deployments-free-per-day")
```

Both windows were widened to bring the worst case to 35 pushes a day:

| Window (IST) | Cadence | Max polls |
|---|---|---|
| 08:30-15:30 main session | 1200s (20 min) | 21 |
| 15:30-22:30 after-market | 1800s (30 min) | 14 |

That leaves roughly two thirds of the daily budget for the gap-backfill crons,
for code pushes, and for manual deploys. `scripts/poll_schedule.py` exports the
worst case as `MAX_POLLS_PER_DAY`, and `tests/test_poll_schedule.py` fails if it
climbs back above two thirds of the cap, so tightening the cadence means
confronting the budget rather than rediscovering it as a broken site.

Nothing is lost by polling less often. `poll_live_news.py` asks NSE for the
whole of the current day on every call and the archive dedups by `seq_id`, so a
slower poll just returns more new items at once. The cost is freshness: an
announcement reaches the site up to one cadence later than before.

Raising the cadence again needs one of: a paid Vercel plan, or serving
`frontdesign/data/` from somewhere that is not the Vercel deployment, so that
data pushes stop costing builds.
