#!/usr/bin/env python3
"""Build per-source prediction JSON + per-day HTML for frontdesign/.

Outputs six independent top-N lists (one per model source) for each
prediction date — they're NOT combined. Each source ranks on its own metric:

  technical   → sort by pred_5d  desc (ml_pipeline/technical, h=1/5/10)        [top 30]
  delivery    → sort by pred_5d  desc (ml_pipeline/delivery,  h=1/5/10)        [top 30]
  multi       → sort by pred_10d desc (ml_pipeline/multi_horizon, h=1..30)     [top 30]
  horizontal  → sort by spike_ratio desc (deliv_spike_scan output)             [top 30]
  confluence  → top-100 deliv_qty(h=5) ∩ top-100 technical(h=10),
                ranked by combined percentile (mirrors confluence_picks.py)    [top 30]
  tech_all4   → multi_horizon technical h=1,5,10,20 — mean percentile rank
                across the 4 horizons (conviction picks)                       [top 200]

Two modes:
  post_mortem (default)  prediction_date = penultimate bhavcopy,
                         result_date     = latest bhavcopy.
                         price_change + correctness computed.
  forecast (--forecast)  prediction_date = latest bhavcopy,
                         result_date     = null (no bhavcopy yet).
                         price_change + correctness left as null.

Outputs:
  frontdesign/data/predictions_<D>.json    per-date payload (never overwritten)
  frontdesign/data/latest.json             pointer at newest post-mortem
  frontdesign/data/forecast.json           pointer at newest forecast (if any)
  frontdesign/data/index.json              all dates + all-time aggregates
  frontdesign/d/<D>.html                   per-day static page (one per date)

Usage:
    .venv/bin/python scripts/build_dashboard_data.py
    .venv/bin/python scripts/build_dashboard_data.py 2026-05-08
    .venv/bin/python scripts/build_dashboard_data.py --forecast
    .venv/bin/python scripts/build_dashboard_data.py --dry-run
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
VENV_SITE = ROOT / ".venv" / "lib" / "python3.13" / "site-packages"
if VENV_SITE.exists():
    sys.path.insert(0, str(VENV_SITE))

import pandas as pd

BHAV_DIR = ROOT / "data" / "bhavcopy" / "bhavcopy"
MTO_DIR = ROOT / "data" / "mto"
SPIKE_DIR = ROOT / "data" / "deliv_spike"
EQUITY_LIST = ROOT / "data" / "nse_equity_list.csv"
ELIGIBLE = ROOT / "data" / "eligible_symbols_cache.json"
OUT_DIR = ROOT / "frontdesign" / "data"
HTML_DIR = ROOT / "frontdesign" / "d"
INDEX_HTML = ROOT / "frontdesign" / "index.html"

TOP_N = 30
SOURCE_TOP_N = {
    "technical": 30,
    "delivery": 30,
    "multi": 30,
    "horizontal": 30,
    "confluence": 30,
    "tech_all4": 200,
}
T_BULL = 0.25
T_BEAR = -0.25
T_SIDE = 0.50
EPS = 0.005


# ---------------- dates ----------------

def parse_yyyymmdd(s: str) -> str:
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def bhav_dates() -> list[str]:
    out = []
    for p in sorted(BHAV_DIR.glob("bhavcopy_*.csv")):
        stem = p.stem.split("_", 1)[1]
        if len(stem) == 8 and stem.isdigit():
            out.append(parse_yyyymmdd(stem))
    return out


def next_business_day(date_iso: str) -> str:
    """Next M-F day after date_iso (best-effort; ignores Indian holidays)."""
    d = datetime.fromisoformat(date_iso).date()
    while True:
        d += timedelta(days=1)
        if d.weekday() < 5:
            return d.isoformat()


def resolve_dates(explicit_pred: str | None, forecast: bool) -> tuple[str, str | None]:
    dates = bhav_dates()
    if not dates:
        raise RuntimeError("No bhavcopy files found")
    if forecast:
        D = explicit_pred or dates[-1]
        if D not in dates:
            raise RuntimeError(f"No bhavcopy for forecast base date {D}")
        return D, next_business_day(D)
    if len(dates) < 2:
        raise RuntimeError(f"Need >=2 bhavcopy files for post-mortem; found {len(dates)}")
    if explicit_pred:
        if explicit_pred not in dates:
            raise RuntimeError(f"No bhavcopy for prediction_date={explicit_pred}")
        idx = dates.index(explicit_pred)
        if idx + 1 >= len(dates):
            raise RuntimeError(f"No result bhavcopy after {explicit_pred} — use --forecast?")
        return explicit_pred, dates[idx + 1]
    return dates[-2], dates[-1]


def load_bhav(date_iso: str) -> pd.DataFrame:
    p = BHAV_DIR / f"bhavcopy_{date_iso.replace('-', '')}.csv"
    df = pd.read_csv(p)
    return df[df["series"] == "EQ"].copy()


# ---------------- company names ----------------

_COMPANY: dict[str, str] | None = None

def company_map() -> dict[str, str]:
    global _COMPANY
    if _COMPANY is None:
        _COMPANY = {}
        if EQUITY_LIST.exists():
            with open(EQUITY_LIST, newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    sym = (row.get("SYMBOL") or "").strip()
                    name = (row.get("NAME OF COMPANY") or "").strip()
                    if sym and name:
                        _COMPANY[sym] = name
    return _COMPANY


# ---------------- prediction sources ----------------

def load_technical(date: str) -> pd.DataFrame:
    try:
        from ml_pipeline.technical.predict import predict_for
        df = predict_for(date)
        keep = [c for c in ("symbol", "pred_1d", "pred_5d", "pred_10d", "close") if c in df.columns]
        return df[keep].copy()
    except Exception as e:
        print(f"[technical] skipped: {e}", file=sys.stderr)
        return pd.DataFrame(columns=["symbol"])


def load_multi(date: str) -> pd.DataFrame:
    try:
        from ml_pipeline.multi_horizon.predict import predict_technical
        df = predict_technical(date)
        keep = [c for c in ("symbol", "pred_1d", "pred_5d", "pred_10d", "close") if c in df.columns]
        return df[keep].copy()
    except Exception as e:
        print(f"[multi] skipped: {e}", file=sys.stderr)
        return pd.DataFrame(columns=["symbol"])


def load_delivery(date: str) -> pd.DataFrame:
    """ml_pipeline.delivery.predict — delivery-feature model, horizons 1/5/10."""
    try:
        from ml_pipeline.delivery.predict import predict_for
        df = predict_for(date)
        keep = [c for c in ("symbol", "pred_1d", "pred_5d", "pred_10d", "close") if c in df.columns]
        return df[keep].copy()
    except Exception as e:
        print(f"[delivery] skipped: {e}", file=sys.stderr)
        return pd.DataFrame(columns=["symbol"])


def load_spikes(date: str) -> pd.DataFrame:
    """First try data/deliv_spike/<date>/spikes_top.csv. Otherwise fall back
    to filtering any spikes_all.csv for rows where date == <date>."""
    p = SPIKE_DIR / date / "spikes_top.csv"
    if p.exists():
        try:
            df = pd.read_csv(p)
            out = df.rename(columns={"ratio": "spike_ratio"})
            keep = [c for c in ("symbol", "spike_ratio", "deliv_qty") if c in out.columns]
            return out[keep]
        except Exception as e:
            print(f"[spikes] error reading {p}: {e}", file=sys.stderr)

    # Fallback: scan all spikes_all.csv files for rows matching this date.
    for scan_dir in sorted(SPIKE_DIR.glob("*/"), reverse=True):
        sa = scan_dir / "spikes_all.csv"
        if not sa.exists():
            continue
        try:
            df = pd.read_csv(sa)
            if "date" not in df.columns:
                continue
            df = df[df["date"] == date]
            if df.empty:
                continue
            out = df.rename(columns={"ratio": "spike_ratio"})
            keep = [c for c in ("symbol", "spike_ratio", "deliv_qty") if c in out.columns]
            # dedupe by symbol, keep strongest spike
            out = out.sort_values("spike_ratio", ascending=False).drop_duplicates("symbol")
            print(f"[spikes] using fallback from {sa.name} ({len(out)} rows for {date})", file=sys.stderr)
            return out[keep]
        except Exception:
            continue

    print(f"[spikes] no data for {date}", file=sys.stderr)
    return pd.DataFrame(columns=["symbol", "spike_ratio", "deliv_qty"])


def load_confluence(date: str, N: int = 100) -> pd.DataFrame:
    """Top-N deliv_qty(h=5) ∩ top-N technical(h=10) — mirrors confluence_picks.py.

    Returns symbol, pred_5d (deliv_qty), pred_10d (technical), close, confluence_score
    (sum of percentile ranks across the two horizons).
    """
    try:
        from ml_pipeline.deliv_qty.predict import predict_for as p_dq
        from ml_pipeline.technical.predict import predict_for as p_tech
    except Exception as e:
        print(f"[confluence] imports failed: {e}", file=sys.stderr)
        return pd.DataFrame(columns=["symbol"])
    try:
        dq = p_dq(date)
        tc = p_tech(date)
    except Exception as e:
        print(f"[confluence] predict failed: {e}", file=sys.stderr)
        return pd.DataFrame(columns=["symbol"])
    if "pred_5d" not in dq.columns or "pred_10d" not in tc.columns:
        print("[confluence] missing pred columns", file=sys.stderr)
        return pd.DataFrame(columns=["symbol"])
    dq_top = dq.sort_values("pred_5d", ascending=False).head(N)
    tc_top = tc.sort_values("pred_10d", ascending=False).head(N)
    overlap = set(dq_top["symbol"]) & set(tc_top["symbol"])
    if not overlap:
        print("[confluence] zero overlap", file=sys.stderr)
        return pd.DataFrame(columns=["symbol"])
    dq_sub = dq_top[["symbol", "close", "pred_5d"] + (["pred_5d_pctile"] if "pred_5d_pctile" in dq_top.columns else [])]
    tc_sub = tc_top[["symbol", "pred_10d"] + (["pred_10d_pctile"] if "pred_10d_pctile" in tc_top.columns else [])]
    m = dq_sub.merge(tc_sub, on="symbol")
    m = m[m["symbol"].isin(overlap)].copy()
    if "pred_5d_pctile" in m.columns and "pred_10d_pctile" in m.columns:
        m["confluence_score"] = m["pred_5d_pctile"] + m["pred_10d_pctile"]
    else:
        m["confluence_score"] = m["pred_5d"].rank(pct=True) + m["pred_10d"].rank(pct=True)
    return m[["symbol", "close", "pred_5d", "pred_10d", "confluence_score"]]


def load_tech_all4(date: str, horizons: tuple[int, ...] = (1, 5, 10, 20)) -> pd.DataFrame:
    """Multi-horizon technical conviction picks: mean percentile rank across
    h=1, h=5, h=10, h=20. Mirrors the `tech_all4` construct from the conviction
    backtests (see ml_pipeline/tests/overlap_4set_summary.md), scaled to top-200
    by composite score rather than strict intersection.
    """
    try:
        from ml_pipeline.multi_horizon.predict import predict_technical
        df = predict_technical(date)
    except Exception as e:
        print(f"[tech_all4] skipped: {e}", file=sys.stderr)
        return pd.DataFrame(columns=["symbol"])
    cols = [f"pred_{h}d" for h in horizons]
    missing = [c for c in cols if c not in df.columns]
    if missing:
        print(f"[tech_all4] missing horizons {missing}", file=sys.stderr)
        return pd.DataFrame(columns=["symbol"])
    df = df.dropna(subset=cols).copy()
    if df.empty:
        return pd.DataFrame(columns=["symbol"])
    pct = pd.concat([df[c].rank(pct=True) for c in cols], axis=1)
    df["all4_score"] = pct.mean(axis=1)
    keep = ["symbol", "close", "all4_score"] + cols
    return df[[c for c in keep if c in df.columns]].copy()


# ---------------- horizontal levels ----------------

_LEVELS_CACHE: dict[tuple[str, str], dict] = {}
_BHAV_HIST_CACHE: dict[str, pd.DataFrame] = {}

def _load_bhav_lite(date_iso: str) -> pd.DataFrame:
    if date_iso not in _BHAV_HIST_CACHE:
        p = BHAV_DIR / f"bhavcopy_{date_iso.replace('-', '')}.csv"
        try:
            df = pd.read_csv(p, usecols=["symbol", "series", "low", "high", "close"])
            df = df[df["series"] == "EQ"][["symbol", "low", "high", "close"]]
            _BHAV_HIST_CACHE[date_iso] = df.set_index("symbol")
        except Exception:
            _BHAV_HIST_CACHE[date_iso] = pd.DataFrame()
    return _BHAV_HIST_CACHE[date_iso]


def horizontal_levels(symbol: str, end_date: str, lookback: int = 20) -> dict:
    key = (symbol, end_date)
    if key in _LEVELS_CACHE:
        return _LEVELS_CACHE[key]
    dates = bhav_dates()
    # take up to `lookback` dates ending at end_date (inclusive)
    eligible = [d for d in dates if d <= end_date][-lookback:]
    lows = []
    highs = []
    for d in eligible:
        df = _load_bhav_lite(d)
        if symbol in df.index:
            r = df.loc[symbol]
            lows.append(float(r["low"]))
            highs.append(float(r["high"]))
    if not lows:
        out = {"support": [], "resistance": []}
    else:
        out = {
            "support": sorted(set(round(v, 2) for v in lows))[:2],
            "resistance": sorted(set(round(v, 2) for v in highs), reverse=True)[:2],
        }
    _LEVELS_CACHE[key] = out
    return out


# ---------------- per-row builder ----------------

def classify_direction(pred_5d: float | None, pred_10d: float | None, spike_only: bool) -> str:
    p5 = pred_5d if pred_5d is not None else 0.0
    p10 = pred_10d if pred_10d is not None else 0.0
    if spike_only and pred_5d is None and pred_10d is None:
        return "bullish"  # spike-only flag implies inflow
    if p5 >= EPS or p10 >= EPS:
        return "bullish"
    if p5 <= -EPS and p10 <= -EPS:
        return "bearish"
    if abs(p5) < EPS and abs(p10) < EPS:
        return "sideways"
    return "bullish" if (p5 + p10) > 0 else "bearish"


def is_correct(direction: str, pct: float | None) -> bool | None:
    if pct is None:
        return None
    if direction == "bullish":
        return pct > T_BULL
    if direction == "bearish":
        return pct < T_BEAR
    return abs(pct) <= T_SIDE


def technical_signals_for(source: str, row: dict) -> list[str]:
    s = []
    if source == "technical":
        s.append("Technical model")
        if row.get("pred_5d") is not None and abs(row["pred_5d"]) >= 0.015:
            s.append("Strong 5d signal")
    elif source == "delivery":
        s.append("Delivery model")
        if row.get("pred_5d") is not None and abs(row["pred_5d"]) >= 0.015:
            s.append("Strong 5d signal")
    elif source == "multi":
        s.append("Multi-horizon model")
        if row.get("pred_10d") is not None and abs(row["pred_10d"]) >= 0.015:
            s.append("Strong 10d signal")
    elif source == "horizontal":
        sr = row.get("spike_ratio") or 0
        s.append(f"Delivery {sr:.1f}x median")
        if sr >= 3:
            s.append("Major spike")
    elif source == "confluence":
        s.append("Delivery × Technical")
        if row.get("confluence_score") is not None and row["confluence_score"] >= 1.7:
            s.append("High-conviction overlap")
    elif source == "tech_all4":
        s.append("Tech ×4 (h=1,5,10,20)")
        score = row.get("all4_score") or 0
        if score >= 0.95:
            s.append("Top 5% all-horizon")
        elif score >= 0.85:
            s.append("Top 15% all-horizon")
    return s


def build_row(source: str, raw_row: dict, pred_date: str, result_close_map: dict, prev_close_map: dict) -> dict:
    sym = raw_row["symbol"]
    p5 = raw_row.get("pred_5d")
    p10 = raw_row.get("pred_10d")
    sr = raw_row.get("spike_ratio")

    # find prices
    prev_close = prev_close_map.get(sym)
    current_price = result_close_map.get(sym)
    if prev_close is None:
        # fall back to model's `close` field which is the prediction-day close
        prev_close = raw_row.get("close")
    if prev_close is None:
        return None

    if current_price is not None:
        change = current_price - prev_close
        pct = (change / prev_close * 100.0) if prev_close else 0.0
    else:
        change = None
        pct = None

    direction = classify_direction(p5, p10, spike_only=(source == "horizontal" and p5 is None and p10 is None))
    correct = is_correct(direction, pct)

    levels = horizontal_levels(sym, pred_date)

    raw = {}
    for k in ("pred_1d", "pred_5d", "pred_10d", "pred_20d", "spike_ratio", "confluence_score", "all4_score"):
        v = raw_row.get(k)
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            raw[k] = round(float(v), 4)

    if source == "tech_all4":
        conf = int(min(100, max(40, round((raw.get("all4_score") or 0) * 100))))
    else:
        conf = int(min(100, 40 + round(abs(raw.get("pred_5d") or raw.get("pred_10d") or raw.get("spike_ratio", 0)) * 100)))

    return {
        "ticker": sym,
        "company": company_map().get(sym, sym),
        "source": source,
        "predicted_direction": direction,
        "previous_close": round(float(prev_close), 2),
        "current_price": round(float(current_price), 2) if current_price is not None else None,
        "price_change": round(float(change), 2) if change is not None else None,
        "price_change_pct": round(float(pct), 2) if pct is not None else None,
        "prediction_correct": correct,
        "technical_signals": technical_signals_for(source, raw_row),
        "multi_factor_score": conf,
        "horizontal_levels": levels,
        "raw": raw,
        "notes": build_notes(source, raw, direction, correct),
    }


def build_notes(source: str, raw: dict, direction: str, correct: bool | None) -> str:
    bits = []
    if "pred_5d" in raw:
        bits.append(f"5d pred {raw['pred_5d']*100:+.2f}%")
    if "pred_10d" in raw:
        bits.append(f"10d pred {raw['pred_10d']*100:+.2f}%")
    if "spike_ratio" in raw:
        bits.append(f"{raw['spike_ratio']:.1f}x median delivery")
    if "confluence_score" in raw:
        bits.append(f"confluence {raw['confluence_score']:.2f}")
    if "all4_score" in raw:
        bits.append(f"all4 score {raw['all4_score']:.2f}")
    if "pred_20d" in raw:
        bits.append(f"20d pred {raw['pred_20d']*100:+.2f}%")
    src_str = {
        "technical": "Technical",
        "delivery": "Delivery",
        "multi": "Multi-horizon",
        "horizontal": "Delivery spike",
        "confluence": "Confluence (deliv × tech)",
        "tech_all4": "Tech ×4 (h=1,5,10,20)",
    }[source]
    head = f"{src_str} → {direction}."
    body = "  ·  ".join(bits) if bits else ""
    tail = ""
    if correct is True:
        tail = "Played out."
    elif correct is False:
        tail = "Missed."
    return f"{head} {body} {tail}".strip()


# ---------------- build per-source lists ----------------

def rank_source(source: str, df: pd.DataFrame, top_n: int) -> list[dict]:
    if df.empty:
        return []
    if source == "technical":
        rank_col = "pred_5d" if "pred_5d" in df.columns else None
    elif source == "delivery":
        rank_col = "pred_5d" if "pred_5d" in df.columns else None
    elif source == "multi":
        rank_col = "pred_10d" if "pred_10d" in df.columns else None
    elif source == "horizontal":
        rank_col = "spike_ratio" if "spike_ratio" in df.columns else None
    elif source == "confluence":
        rank_col = "confluence_score" if "confluence_score" in df.columns else None
    elif source == "tech_all4":
        rank_col = "all4_score" if "all4_score" in df.columns else None
    else:
        rank_col = None
    if rank_col is None:
        return []
    df = df.dropna(subset=[rank_col])
    if df.empty:
        return []
    return df.sort_values(rank_col, ascending=False).head(top_n).to_dict(orient="records")


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {"total": 0, "correct": 0, "accuracy_pct": None, "avg_move_pct": None, "best": None, "worst": None}
    with_results = [r for r in rows if r.get("price_change_pct") is not None]
    if not with_results:
        # forecast mode — no results yet
        return {"total": len(rows), "correct": None, "accuracy_pct": None, "avg_move_pct": None, "best": None, "worst": None}
    correct = sum(1 for r in with_results if r.get("prediction_correct"))
    moves = [r["price_change_pct"] for r in with_results]
    best = max(with_results, key=lambda r: r["price_change_pct"])
    worst = min(with_results, key=lambda r: r["price_change_pct"])
    return {
        "total": len(rows),
        "correct": correct,
        "accuracy_pct": round(correct / len(with_results) * 100, 1),
        "avg_move_pct": round(sum(moves) / len(moves), 2),
        "best": {"ticker": best["ticker"], "pct": best["price_change_pct"]},
        "worst": {"ticker": worst["ticker"], "pct": worst["price_change_pct"]},
    }


def build_per_source(pred_date: str, result_date: str | None, top_n: int) -> dict:
    tech = load_technical(pred_date)
    deliv = load_delivery(pred_date)
    multi = load_multi(pred_date)
    spikes = load_spikes(pred_date)
    confl = load_confluence(pred_date)
    all4 = load_tech_all4(pred_date)

    print(f"[sources] tech={len(tech)} deliv={len(deliv)} multi={len(multi)} spikes={len(spikes)} confl={len(confl)} tech_all4={len(all4)}", file=sys.stderr)

    # closes
    if result_date:
        try:
            bhav_r = load_bhav(result_date).set_index("symbol")
            result_close = bhav_r["close"].to_dict()
            prev_close = bhav_r["prev_close"].to_dict()
        except FileNotFoundError:
            result_close = {}
            prev_close = {}
    else:
        result_close = {}
        try:
            prev_close = load_bhav(pred_date).set_index("symbol")["close"].to_dict()
        except FileNotFoundError:
            prev_close = {}
    # fallback prev_close from prediction-day bhavcopy
    try:
        prev_d = load_bhav(pred_date).set_index("symbol")["close"].to_dict()
        for sym, v in prev_d.items():
            prev_close.setdefault(sym, v)
    except FileNotFoundError:
        pass

    by_source = {}
    for src, df in (("technical", tech), ("delivery", deliv), ("multi", multi),
                    ("horizontal", spikes), ("confluence", confl), ("tech_all4", all4)):
        n = SOURCE_TOP_N.get(src, top_n)
        raws = rank_source(src, df, n)
        rows = []
        for raw in raws:
            row = build_row(src, raw, pred_date, result_close, prev_close)
            if row is not None:
                rows.append(row)
        by_source[src] = {"summary": summarize(rows), "predictions": rows}
        print(f"[{src}] ranked {len(rows)} rows", file=sys.stderr)

    return by_source


# ---------------- per-day HTML ----------------

PAGE_TEMPLATE = """<!doctype html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Stockbot · {pred_date} → {result_label}</title>
  <link rel="stylesheet" href="../styles.css" />
</head>
<body>
  <header class="topbar">
    <div class="topbar__brand">
      <a href="../index.html" style="text-decoration:none;color:inherit;display:flex;align-items:center;gap:12px;">
        <span class="brand__mark">SB</span>
        <div class="brand__label">
          <div class="brand__title">Stockbot Review</div>
          <div class="brand__sub">Pred: {pred_date} → Result: {result_label}</div>
        </div>
      </a>
    </div>
    <div class="topbar__center">
      <a href="../index.html" class="alltime" style="text-decoration:none;">← all days</a>
    </div>
    <div class="topbar__right">
      <button id="themeToggle" class="iconbtn" aria-label="Toggle theme">◐</button>
    </div>
  </header>

  <div class="layout">
    <aside class="sidebar">
      <div class="sidebar__hdr"><div>Volume watch · 7d</div><span class="muted" id="vwCount">0</span></div>
      <ul class="vwlist" id="vwList"></ul>
      <div class="sidebar__hdr sidebar__hdr--sub"><div>Sources today</div></div>
      <div class="srcsumm" id="srcSumm"></div>
      <div class="sidebar__hdr sidebar__hdr--sub"><div>History</div><span class="muted" id="sidebarCount">0</span></div>
      <ul class="datelist" id="dateList"></ul>
    </aside>
    <main class="main">
      <div class="srctabs" id="srcTabs"></div>
      <section class="cards" id="summaryCards"></section>
      <section class="filters">
        <div class="filter"><label>Direction</label>
          <select id="filterDir"><option value="all">All</option><option value="bullish">Bullish</option><option value="bearish">Bearish</option><option value="sideways">Sideways</option></select>
        </div>
        <div class="filter"><label>Min confidence <span id="confVal">0</span></label>
          <input type="range" id="filterConf" min="0" max="100" value="0" />
        </div>
        <div class="filter"><label>Sort</label>
          <select id="sortBy"><option value="default">Default (model rank)</option><option value="gain">Biggest gain</option><option value="loss">Biggest loss</option><option value="accuracy">Correct first</option><option value="confidence">Confidence ↓</option></select>
        </div>
        <div class="filter filter--note">
          <span class="legend"><i class="dot dot--green"></i> Correct</span>
          <span class="legend"><i class="dot dot--red"></i> Wrong</span>
          <span class="legend"><i class="dot dot--grey"></i> Sideways / no result</span>
        </div>
      </section>
      <section class="tablewrap">
        <table class="ptable" id="ptable">
          <thead><tr><th>#</th><th>Ticker</th><th>Company</th><th>Dir</th><th class="num">Prev close</th><th class="num">Last</th><th class="num">Δ</th><th class="num">%</th><th>Result</th><th>Signals</th><th class="num">Conf</th><th>S / R</th><th class="grow">Notes</th></tr></thead>
          <tbody id="rows"></tbody>
        </table>
      </section>
      <footer class="footer">
        <span class="muted">Stockbot · static snapshot for {pred_date}</span>
        <span class="muted" id="generatedAt"></span>
      </footer>
    </main>
  </div>
  <script>window.SB_DATA_URL = "../data/{json_name}";</script>
  <script src="../app.js"></script>
</body>
</html>
"""


def write_per_day_html(pred_date: str, result_date: str | None, json_name: str) -> Path:
    HTML_DIR.mkdir(parents=True, exist_ok=True)
    result_label = result_date if result_date else f"{pred_date} (forecast)"
    html = PAGE_TEMPLATE.format(pred_date=pred_date, result_label=result_label, json_name=json_name)
    out = HTML_DIR / f"{pred_date}.html"
    out.write_text(html)
    return out


# ---------------- volume watch ----------------

def _eligible_universe(min_price: float = 50.0, min_mcap_cr: float = 500.0) -> set[str]:
    if not ELIGIBLE.exists():
        return set()
    data = json.loads(ELIGIBLE.read_text())
    keep = set()
    for sym, meta in data.items():
        if not isinstance(meta, dict):
            continue
        price = meta.get("price")
        mcap = meta.get("market_cap_cr")
        if price is None or mcap is None:
            continue
        if price >= min_price and mcap >= min_mcap_cr:
            keep.add(sym)
    return keep


def build_volume_watch(latest_date: str, lookback_days: int = 7,
                       baseline_days: int = 60, ratio_thresh: float = 2.0,
                       min_qty: int = 50_000, top_n: int = 25) -> dict:
    """Rolling N-day delivery-spike watchlist.

    For each of the last `lookback_days` trading days, find stocks where
    deliverable_qty >= ratio_thresh * trailing 60d median. For each unique
    symbol, keep the strongest spike. Then look up the close on spike-day
    vs latest close from bhavcopy → pct_since_spike.

    Returns:
      {
        "generated_at": ..., "latest_date": ..., "lookback_days": 7,
        "watches": [ { ticker, company, spike_date, days_since,
                       spike_ratio, deliv_qty, baseline_median,
                       spike_close, latest_close, pct_since_spike }, ... ]
      }
    """
    import statistics

    universe = _eligible_universe()
    if not universe:
        print("[volwatch] empty universe — skipping", file=sys.stderr)
        return {"generated_at": datetime.now().isoformat(timespec="seconds"),
                "latest_date": latest_date, "lookback_days": lookback_days, "watches": []}

    mto_files = sorted(MTO_DIR.glob("mto_*.csv"))
    if len(mto_files) < baseline_days + lookback_days:
        print(f"[volwatch] only {len(mto_files)} MTO files; using what's available", file=sys.stderr)
    window = mto_files[-(baseline_days + lookback_days):]
    scan_files = window[-lookback_days:]
    scan_dates = {f"{f.stem.split('_')[1][:4]}-{f.stem.split('_')[1][4:6]}-{f.stem.split('_')[1][6:8]}" for f in scan_files}

    # Build per-symbol time series of deliv_qty across the window
    series: dict[str, list[tuple[str, float]]] = {}
    for f in window:
        stem = f.stem.split("_", 1)[1]
        d = f"{stem[:4]}-{stem[4:6]}-{stem[6:8]}"
        try:
            df = pd.read_csv(f, usecols=["symbol", "series", "deliverable_qty"])
        except Exception:
            continue
        df = df[(df["series"] == "EQ") & (df["symbol"].isin(universe))]
        df["deliverable_qty"] = pd.to_numeric(df["deliverable_qty"], errors="coerce")
        for sym, q in zip(df["symbol"], df["deliverable_qty"]):
            if pd.isna(q):
                continue
            series.setdefault(sym, []).append((d, float(q)))

    # Score each symbol's strongest spike in the scan window
    raw_spikes = []
    for sym, ts in series.items():
        ts.sort()
        for i, (d, q) in enumerate(ts):
            if d not in scan_dates or q < min_qty:
                continue
            tail = [v for _, v in ts[max(0, i - baseline_days):i]]
            if len(tail) < max(20, baseline_days // 3):
                continue
            med = statistics.median(tail)
            if med <= 0:
                continue
            r = q / med
            if r >= ratio_thresh:
                raw_spikes.append({"symbol": sym, "spike_date": d, "deliv_qty": int(q),
                                   "baseline_median": int(med), "spike_ratio": round(r, 2)})

    if not raw_spikes:
        print("[volwatch] no spikes found", file=sys.stderr)
        return {"generated_at": datetime.now().isoformat(timespec="seconds"),
                "latest_date": latest_date, "lookback_days": lookback_days, "watches": []}

    # Dedup by symbol — keep strongest ratio
    by_sym: dict[str, dict] = {}
    for s in raw_spikes:
        cur = by_sym.get(s["symbol"])
        if cur is None or s["spike_ratio"] > cur["spike_ratio"]:
            by_sym[s["symbol"]] = s

    # Hydrate with prices from bhavcopy
    latest_bhav = load_bhav(latest_date).set_index("symbol")
    latest_close_map = latest_bhav["close"].to_dict()
    company = company_map()

    watches = []
    for sym, s in by_sym.items():
        spike_d = s["spike_date"]
        # close on spike day
        try:
            bd = load_bhav(spike_d).set_index("symbol")
            spike_close = float(bd.loc[sym, "close"]) if sym in bd.index else None
        except FileNotFoundError:
            spike_close = None
        latest_close = float(latest_close_map[sym]) if sym in latest_close_map else None
        pct = None
        if spike_close and latest_close:
            pct = round((latest_close - spike_close) / spike_close * 100, 2)
        days_since = (datetime.fromisoformat(latest_date) - datetime.fromisoformat(spike_d)).days
        watches.append({
            "ticker": sym,
            "company": company.get(sym, sym),
            "spike_date": spike_d,
            "days_since": days_since,
            "spike_ratio": s["spike_ratio"],
            "deliv_qty": s["deliv_qty"],
            "baseline_median": s["baseline_median"],
            "spike_close": round(spike_close, 2) if spike_close else None,
            "latest_close": round(latest_close, 2) if latest_close else None,
            "pct_since_spike": pct,
        })
    # Sort by spike ratio desc (strongest signals first), then by recency
    watches.sort(key=lambda w: (-w["spike_ratio"], -w["days_since"]))
    watches = watches[:top_n]

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "latest_date": latest_date,
        "lookback_days": lookback_days,
        "ratio_threshold": ratio_thresh,
        "watches": watches,
    }


# ---------------- index.json ----------------

def update_index(pred_date: str, result_date: str | None, mode: str, by_source: dict, filename: str, html_filename: str) -> dict:
    idx_path = OUT_DIR / "index.json"
    if idx_path.exists():
        idx = json.loads(idx_path.read_text())
    else:
        idx = {"all_time": {"days": 0, "total_calls": 0, "correct": 0, "accuracy_pct": 0.0}, "dates": []}
    idx["dates"] = [d for d in idx["dates"] if d.get("date") != pred_date]
    total = sum(s["summary"]["total"] for s in by_source.values())
    # accuracy = combined across sources but only counting rows with results
    correct_sum, attempted_sum = 0, 0
    for s in by_source.values():
        if s["summary"].get("accuracy_pct") is not None:
            with_results = sum(1 for p in s["predictions"] if p.get("price_change_pct") is not None)
            correct_sum += s["summary"]["correct"] or 0
            attempted_sum += with_results
    acc = round(correct_sum / attempted_sum * 100, 1) if attempted_sum else None
    idx["dates"].append({
        "date": pred_date,
        "result_date": result_date,
        "mode": mode,
        "total": total,
        "correct": correct_sum if attempted_sum else None,
        "accuracy_pct": acc,
        "file": filename,
        "page": html_filename,
    })
    idx["dates"].sort(key=lambda d: d["date"], reverse=True)
    # all-time (post-mortem only)
    pm = [d for d in idx["dates"] if d.get("mode") == "post_mortem" and d.get("accuracy_pct") is not None]
    if pm:
        tc = sum(d["total"] for d in pm)
        cc = sum(d["correct"] for d in pm)
        idx["all_time"] = {
            "days": len(pm),
            "total_calls": tc,
            "correct": cc,
            "accuracy_pct": round(cc / tc * 100, 1) if tc else 0.0,
        }
    return idx


# ---------------- main ----------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("prediction_date", nargs="?", default=None,
                    help="YYYY-MM-DD; defaults to latest available")
    ap.add_argument("--top", type=int, default=TOP_N)
    ap.add_argument("--forecast", action="store_true",
                    help="Use latest bhavcopy as base; no result data yet")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    pred_date, result_date = resolve_dates(args.prediction_date, args.forecast)
    mode = "forecast" if args.forecast else "post_mortem"
    print(f"[dates] mode={mode}  prediction={pred_date}  result={result_date}", file=sys.stderr)

    by_source = build_per_source(pred_date, result_date if not args.forecast else None, args.top)

    payload = {
        "prediction_date": pred_date,
        "result_date": result_date,
        "mode": mode,
        "currency": "INR",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "by_source": by_source,
    }

    if args.dry_run:
        print(json.dumps({k: (v if k != "by_source" else {s: {"summary": d["summary"], "n": len(d["predictions"])} for s, d in v.items()}) for k, v in payload.items()}, indent=2))
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    json_name = f"predictions_{pred_date}{'_forecast' if args.forecast else ''}.json"
    out_path = OUT_DIR / json_name
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"[wrote] {out_path}", file=sys.stderr)

    pointer = OUT_DIR / ("forecast.json" if args.forecast else "latest.json")
    pointer.write_text(json.dumps(payload, indent=2))
    print(f"[wrote] {pointer}", file=sys.stderr)

    html_path = write_per_day_html(pred_date, result_date if not args.forecast else None, json_name)
    print(f"[wrote] {html_path}", file=sys.stderr)

    idx = update_index(pred_date, result_date if not args.forecast else None, mode, by_source, json_name, f"d/{pred_date}.html")
    (OUT_DIR / "index.json").write_text(json.dumps(idx, indent=2))
    print(f"[wrote] {OUT_DIR / 'index.json'}", file=sys.stderr)

    # Rolling 7-day volume watch — uses the most recent bhavcopy date as "latest"
    latest_for_watch = bhav_dates()[-1]
    vw = build_volume_watch(latest_for_watch, lookback_days=7)
    (OUT_DIR / "volume_watch.json").write_text(json.dumps(vw, indent=2))
    print(f"[wrote] {OUT_DIR / 'volume_watch.json'} ({len(vw['watches'])} watches)", file=sys.stderr)

    # log per-source summary
    for s, blk in by_source.items():
        summ = blk["summary"]
        if summ.get("accuracy_pct") is not None:
            print(f"  {s:<11}: {summ['correct']}/{summ['total']} = {summ['accuracy_pct']}%  avg={summ['avg_move_pct']}%", file=sys.stderr)
        else:
            print(f"  {s:<11}: {summ['total']} forecasts (no results yet)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
