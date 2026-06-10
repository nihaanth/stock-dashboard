#!/usr/bin/env python3
"""
Build a per-industry / per-stock folder tree from the eligible NSE universe.

For each eligible stock (price >= 50, mcap >= 500cr), writes:
  data/industries/<industry-slug>/<SYMBOL>.json
    - symbol/industry meta + latest snapshot
    - daily series for the last ~3 months
      (close, prev_close, volume, deliverable_qty, delivery_pct)
    - all NSE announcements for the symbol over the last 3 months
      (sort_date, an_dt, desc, sm_name, attchmntFile URL)

Also writes:
  data/industries/industries.json        global index
  data/industries/<slug>/index.json      per-industry stock index

Full rebuild each run (idempotent).

Usage:
    python scripts/build_industry_folders.py
        [--min-price 50] [--min-mcap-cr 500]
        [--as-of YYYY-MM-DD] [--window-days 90]
        [--universe-file data/stock_master.json]
"""
from __future__ import annotations

import argparse
import glob
import json
import plistlib
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
MTO_DIR = ROOT / "data" / "mto"
BHAV_DIR = ROOT / "data" / "bhavcopy" / "bhavcopy"
WEBARCHIVE_DIR = ROOT / "data" / "years data"
MASTER = ROOT / "data" / "stock_master.json"
DEFAULT_OUT_BASE = ROOT / "frontdesign" / "data" / "industries"


def slugify(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    return s.strip("-") or "unknown"


def load_universe(master_path: Path, min_price: float, min_mcap_cr: float) -> dict:
    raw = json.loads(master_path.read_text())
    out = {}
    for sym, meta in raw.items():
        price = meta.get("price")
        mcap = meta.get("market_cap_cr")
        industry = meta.get("industry")
        if price is None or mcap is None or industry is None:
            continue
        if industry.strip() in ("", "-"):
            continue
        if price < min_price or mcap < min_mcap_cr:
            continue
        out[sym] = {
            "name": meta.get("name") or sym,
            "industry": industry,
            "sector": meta.get("sector"),
            "isin": meta.get("isin"),
            "price": float(price),
            "market_cap_cr": float(mcap),
            "industry_source": meta.get("industry_source"),
        }
    return out


def files_in_window(directory: Path, glob_pat: str,
                    start: datetime, end: datetime) -> list[Path]:
    """Return CSV paths where the YYYYMMDD in filename falls in [start, end]."""
    out: list[Path] = []
    for f in sorted(Path(p) for p in glob.glob(str(directory / glob_pat))):
        m = re.search(r"(\d{8})", f.name)
        if not m:
            continue
        try:
            d = datetime.strptime(m.group(1), "%Y%m%d")
        except ValueError:
            continue
        if start <= d <= end:
            out.append(f)
    return out


def load_recent_bhav(symbols: set[str], start: datetime, end: datetime) -> pd.DataFrame:
    files = files_in_window(BHAV_DIR, "bhavcopy_*.csv", start, end)
    if not files:
        return pd.DataFrame(columns=["symbol", "date", "close", "prev_close", "volume"])
    print(f"[bhav] {len(files)} files  {files[0].name} .. {files[-1].name}")
    parts = []
    for f in files:
        try:
            df = pd.read_csv(f, usecols=["symbol", "series", "date",
                                         "close", "prev_close", "volume"])
        except Exception as e:
            print(f"  skip {f.name}: {e}")
            continue
        # EQ + BE: BE is the Trade-to-Trade segment — real OHLC, just a different
        # settlement series. Excluding it left BE-only names (e.g. SICALLOG,
        # SWANDEF, VHLTD) with empty daily data and a blank price graph.
        df = df[df["series"].isin(("EQ", "BE")) & (df["symbol"].isin(symbols))]
        if not df.empty:
            parts.append(df)
    if not parts:
        return pd.DataFrame(columns=["symbol", "date", "close", "prev_close", "volume"])
    out = pd.concat(parts, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"], format="mixed", errors="coerce")
    for c in ("close", "prev_close", "volume"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out.dropna(subset=["date", "close"])
    # A symbol trades in one series per day; dedup defensively now that we accept
    # two series, so the MTO left-merge can't fan out on (symbol, date).
    out = out.drop_duplicates(subset=["symbol", "date"], keep="last")
    return out[["symbol", "date", "close", "prev_close", "volume"]]


def load_recent_mto(symbols: set[str], start: datetime, end: datetime) -> pd.DataFrame:
    files = files_in_window(MTO_DIR, "mto_*.csv", start, end)
    if not files:
        return pd.DataFrame(columns=["symbol", "date", "deliverable_qty", "delivery_pct"])
    print(f"[mto]  {len(files)} files  {files[0].name} .. {files[-1].name}")
    parts = []
    for f in files:
        try:
            df = pd.read_csv(f, usecols=["symbol", "series", "date",
                                         "deliverable_qty", "delivery_pct"])
        except Exception as e:
            print(f"  skip {f.name}: {e}")
            continue
        df = df[df["series"].isin(("EQ", "BE")) & (df["symbol"].isin(symbols))]
        if not df.empty:
            parts.append(df)
    if not parts:
        return pd.DataFrame(columns=["symbol", "date", "deliverable_qty", "delivery_pct"])
    out = pd.concat(parts, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"], format="%Y%m%d", errors="coerce")
    for c in ("deliverable_qty", "delivery_pct"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out.dropna(subset=["date"])
    out = out.drop_duplicates(subset=["symbol", "date"], keep="last")
    return out[["symbol", "date", "deliverable_qty", "delivery_pct"]]


def build_daily_map(symbols: set[str], start: datetime, end: datetime) -> dict[str, list[dict]]:
    bhav = load_recent_bhav(symbols, start, end)
    mto = load_recent_mto(symbols, start, end)
    df = bhav.merge(mto, on=["symbol", "date"], how="left")
    df = df.sort_values(["symbol", "date"]).reset_index(drop=True)
    out: dict[str, list[dict]] = {}
    for sym, g in df.groupby("symbol", sort=False):
        rows = []
        for _, r in g.iterrows():
            rows.append({
                "date": r["date"].strftime("%Y-%m-%d"),
                "close": None if pd.isna(r["close"]) else round(float(r["close"]), 2),
                "prev_close": None if pd.isna(r["prev_close"]) else round(float(r["prev_close"]), 2),
                "volume": None if pd.isna(r["volume"]) else int(r["volume"]),
                "deliverable_qty": None if pd.isna(r["deliverable_qty"]) else int(r["deliverable_qty"]),
                "delivery_pct": None if pd.isna(r["delivery_pct"]) else round(float(r["delivery_pct"]), 2),
            })
        out[sym] = rows
    return out


def _unescape_html_fixpoint(s: str) -> str:
    """Collapse stacked html.escape(..., quote=False) layers.

    Legacy webarchives were re-escaped on every nightly update (the extract
    side never undid what the write side escaped), so strings carry many
    stacked levels ("Media &amp;amp;... Entertainment"). Each replace pass
    strictly shrinks the string, so the loop always terminates. Targeted
    3-entity replace, not html.unescape(), so genuine entities in NSE text
    are left alone.
    """
    while True:
        t = s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        if t == s:
            return t
        s = t


def extract_json_from_webarchive(path: Path) -> list[dict]:
    """Copied pattern from scripts/update_webarchive.py."""
    with open(path, "rb") as f:
        data = plistlib.load(f)
    html_bytes = data["WebMainResource"]["WebResourceData"]
    try:
        html_str = html_bytes.decode("utf-8")
    except UnicodeDecodeError:
        html_str = html_bytes.decode("latin-1")
    m = re.search(r"<pre[^>]*>(.*?)</pre>", html_str, re.DOTALL)
    if not m:
        raise ValueError(f"no <pre> tag in {path}")
    records = json.loads(m.group(1))
    for r in records:
        for k, v in r.items():
            if isinstance(v, str) and "&" in v:
                r[k] = _unescape_html_fixpoint(v)
    return records


def build_news_map(symbols: set[str], start: datetime, end: datetime) -> dict[str, list[dict]]:
    years_needed = sorted({start.year, end.year})
    files = [WEBARCHIVE_DIR / f"{y}.webarchive" for y in years_needed]
    files = [f for f in files if f.exists()]
    print(f"[news] reading {len(files)} webarchive(s): {[f.name for f in files]}")
    all_records: list[dict] = []
    for f in files:
        all_records.extend(extract_json_from_webarchive(f))
    cutoff_start = start.strftime("%Y-%m-%d %H:%M:%S")
    cutoff_end = end.strftime("%Y-%m-%d 23:59:59")
    out: dict[str, list[dict]] = {}
    for r in all_records:
        sym = r.get("symbol")
        if sym not in symbols:
            continue
        sd = r.get("sort_date")
        if not sd or sd < cutoff_start or sd > cutoff_end:
            continue
        out.setdefault(sym, []).append({
            "seq_id": r.get("seq_id"),  # unique key for live-feed dedup on the front-end
            "sort_date": sd,
            "an_dt": r.get("an_dt"),
            "desc": r.get("desc"),
            "sm_name": r.get("sm_name"),
            "attchmntFile": r.get("attchmntFile"),
        })
    for sym in out:
        out[sym].sort(key=lambda d: d["sort_date"] or "", reverse=True)
    print(f"[news] {len(out):,} / {len(symbols):,} eligible symbols have >=1 announcement")
    return out


def assign_industry_slugs(universe: dict) -> dict[str, str]:
    industries = sorted({meta["industry"] for meta in universe.values()})
    slug_to_industry: dict[str, str] = {}
    industry_to_slug: dict[str, str] = {}
    for ind in industries:
        slug = slugify(ind)
        if slug in slug_to_industry and slug_to_industry[slug] != ind:
            i = 1
            while f"{slug}-{i}" in slug_to_industry:
                i += 1
            slug = f"{slug}-{i}"
        slug_to_industry[slug] = ind
        industry_to_slug[ind] = slug
    return industry_to_slug


def _pct_change(daily: list[dict]) -> float | None:
    """% change from first to last close in the window. None if <2 points."""
    closes = [d["close"] for d in daily if d.get("close") is not None]
    if len(closes) < 2 or closes[0] == 0:
        return None
    return round((closes[-1] - closes[0]) / closes[0] * 100.0, 2)


def _median(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    xs = sorted(xs)
    n = len(xs)
    mid = n // 2
    return round((xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2), 2)


def industry_aggregates(symbols: list[str],
                        daily_map: dict[str, list[dict]],
                        news_map: dict[str, list[dict]]) -> dict:
    """Per-industry rollup metrics for the landing grid."""
    pct_changes: list[float] = []
    latest_deliveries: list[float] = []
    n_up = 0
    n_with_change = 0
    news_count = 0
    for sym in symbols:
        rows = daily_map.get(sym, [])
        pc = _pct_change(rows)
        if pc is not None:
            pct_changes.append(pc)
            n_with_change += 1
            if pc > 0:
                n_up += 1
        # latest non-null delivery_pct over the window
        for r in reversed(rows):
            dp = r.get("delivery_pct")
            if dp is not None:
                latest_deliveries.append(dp)
                break
        news_count += len(news_map.get(sym, []))
    pct_up = round(n_up / n_with_change * 100.0, 1) if n_with_change else None
    return {
        "median_pct_chg_90d": _median(pct_changes),
        "pct_stocks_up": pct_up,
        "median_delivery_pct": _median(latest_deliveries),
        "news_count_90d": news_count,
    }


def write_tree(universe: dict,
               daily_map: dict[str, list[dict]],
               news_map: dict[str, list[dict]],
               as_of: datetime,
               window_days: int,
               min_price: float,
               min_mcap_cr: float,
               out_base: Path) -> None:
    # Build the entire tree into a sibling ".tmp" dir, then swap it into place at
    # the very end. If any write fails mid-build, the live tree is left untouched
    # instead of deleted-then-half-rebuilt (which blanks the dashboard).
    build_base = out_base.parent / (out_base.name + ".tmp")
    if build_base.exists():
        shutil.rmtree(build_base)
    build_base.mkdir(parents=True)

    industry_to_slug = assign_industry_slugs(universe)
    by_industry: dict[str, list[str]] = {}
    for sym, meta in universe.items():
        by_industry.setdefault(meta["industry"], []).append(sym)

    industries_summary: list[dict] = []
    stock_index: list[dict] = []
    n_stocks_total = 0

    for industry, symbols in sorted(by_industry.items(), key=lambda kv: -len(kv[1])):
        slug = industry_to_slug[industry]
        ind_dir = build_base / slug
        ind_dir.mkdir(parents=True, exist_ok=True)

        stock_index_rows: list[dict] = []
        for sym in sorted(symbols):
            meta = universe[sym]
            payload = {
                "symbol": sym,
                "name": meta["name"],
                "industry": industry,
                "industry_slug": slug,
                "sector": meta["sector"],
                "isin": meta["isin"],
                "price": meta["price"],
                "market_cap_cr": meta["market_cap_cr"],
                "industry_source": meta["industry_source"],
                "as_of": as_of.strftime("%Y-%m-%d"),
                "window_days": window_days,
                "daily": daily_map.get(sym, []),
                "news": news_map.get(sym, []),
            }
            (ind_dir / f"{sym}.json").write_text(json.dumps(payload, indent=2))
            stock_index_rows.append({
                "symbol": sym,
                "name": meta["name"],
                "price": meta["price"],
                "market_cap_cr": meta["market_cap_cr"],
                "pct_chg_90d": _pct_change(daily_map.get(sym, [])),
                "news_count": len(news_map.get(sym, [])),
                "file": f"{sym}.json",
            })
            stock_index.append({
                "symbol": sym,
                "name": meta["name"],
                "industry_slug": slug,
            })

        n_stocks_total += len(stock_index_rows)
        (ind_dir / "index.json").write_text(json.dumps({
            "industry": industry,
            "slug": slug,
            "as_of": as_of.strftime("%Y-%m-%d"),
            "n_stocks": len(stock_index_rows),
            "stocks": stock_index_rows,
        }, indent=2))
        agg = industry_aggregates(symbols, daily_map, news_map)
        industries_summary.append({
            "slug": slug,
            "name": industry,
            "n_stocks": len(stock_index_rows),
            "path": f"{slug}/index.json",
            **agg,
        })

    (build_base / "industries.json").write_text(json.dumps({
        "as_of": as_of.strftime("%Y-%m-%d"),
        "window_days": window_days,
        "universe_filter": {"min_price": min_price, "min_mcap_cr": min_mcap_cr},
        "total_stocks": n_stocks_total,
        "n_industries": len(industries_summary),
        "industries": industries_summary,
        "stock_index": stock_index,
    }, indent=2))

    # Swap the freshly-built tree into place. Remove the live tree only now, once
    # the new one is complete, then rename (atomic on the same filesystem).
    if out_base.exists():
        shutil.rmtree(out_base)
    build_base.replace(out_base)

    try:
        rel = out_base.relative_to(ROOT)
    except ValueError:
        rel = out_base
    print(f"[done] {n_stocks_total} stocks across {len(industries_summary)} industries "
          f"-> {rel}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--min-price", type=float, default=50.0)
    p.add_argument("--min-mcap-cr", type=float, default=500.0)
    p.add_argument("--as-of", type=str, default=None,
                   help="anchor date YYYY-MM-DD; defaults to today")
    p.add_argument("--window-days", type=int, default=90,
                   help="news + daily lookback in calendar days")
    p.add_argument("--universe-file", type=Path, default=MASTER)
    p.add_argument("--output", type=Path, default=DEFAULT_OUT_BASE,
                   help=f"output directory (default: {DEFAULT_OUT_BASE.relative_to(ROOT)})")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    as_of = datetime.strptime(args.as_of, "%Y-%m-%d") if args.as_of else datetime.now()
    as_of = as_of.replace(hour=0, minute=0, second=0, microsecond=0)
    start = as_of - timedelta(days=args.window_days)
    print(f"[*] universe filter: price>={args.min_price}, mcap_cr>={args.min_mcap_cr}")
    print(f"[*] window: {start.date()} .. {as_of.date()} ({args.window_days} calendar days)")

    universe = load_universe(args.universe_file, args.min_price, args.min_mcap_cr)
    n_industries = len({m["industry"] for m in universe.values()})
    print(f"[*] {len(universe):,} eligible symbols, {n_industries} distinct industries")

    daily_map = build_daily_map(set(universe), start, as_of)
    print(f"[daily] {len(daily_map):,} / {len(universe):,} eligible symbols have >=1 row")

    news_map = build_news_map(set(universe), start, as_of)

    write_tree(universe, daily_map, news_map, as_of, args.window_days,
               args.min_price, args.min_mcap_cr, args.output)


if __name__ == "__main__":
    main()
