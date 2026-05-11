"""Confluence picks — top-100 of deliv_qty(h=5) INTERSECT top-100 of technical(h=10).

Asks for a date, checks bhavcopy + MTO files exist for that day, runs both
model rankings, prints the overlap.

Usage:
    python confluence_picks.py
    python confluence_picks.py 2026-05-08
"""

from __future__ import annotations

import sys
from datetime import datetime, date
from pathlib import Path

ROOT = Path(__file__).parent
BHAV_DIR = ROOT / "data" / "bhavcopy" / "bhavcopy"
MTO_DIR = ROOT / "data" / "mto"


def parse_date(s: str) -> date | None:
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def check_data(d: date) -> dict:
    ymd = d.strftime("%Y%m%d")
    bhav = BHAV_DIR / f"bhavcopy_{ymd}.csv"
    mto = MTO_DIR / f"mto_{ymd}.csv"
    return {
        "bhav_path": bhav,
        "bhav_exists": bhav.exists(),
        "bhav_size_kb": round(bhav.stat().st_size / 1024, 1) if bhav.exists() else None,
        "mto_path": mto,
        "mto_exists": mto.exists(),
        "mto_size_kb": round(mto.stat().st_size / 1024, 1) if mto.exists() else None,
    }


def main():
    if len(sys.argv) > 1:
        raw = sys.argv[1]
    else:
        raw = input("Date (YYYY-MM-DD, blank = latest): ").strip()

    if not raw:
        target = None
        target_str = "latest"
    else:
        target = parse_date(raw)
        if target is None:
            print(f"ERROR: unparseable date '{raw}'. Try YYYY-MM-DD.")
            sys.exit(1)
        target_str = target.isoformat()

    print(f"\n=== Data availability for {target_str} ===")
    if target is not None:
        info = check_data(target)
        if info["bhav_exists"]:
            print(f"  bhavcopy : YES  {info['bhav_path'].name}  ({info['bhav_size_kb']} KB)")
        else:
            print(f"  bhavcopy : NO   {info['bhav_path'].name}  -- MISSING")
        if info["mto_exists"]:
            print(f"  mto      : YES  {info['mto_path'].name}  ({info['mto_size_kb']} KB)")
        else:
            print(f"  mto      : NO   {info['mto_path'].name}  -- MISSING")
        if not (info["bhav_exists"] and info["mto_exists"]):
            print("\nCannot rank — one or both data files missing for this date.")
            print("Likely cause: weekend / holiday / data not yet ingested.")
            sys.exit(2)
    else:
        print("  (skipping file check — using latest available)")

    print("\n=== Loading models + scoring universe ===")
    from ml_pipeline.deliv_qty.predict import predict_for as p_dq
    from ml_pipeline.technical.predict import predict_for as p_tech

    dq = p_dq(target_str if target else None)
    tc = p_tech(target_str if target else None)

    actual_date = dq["date"].iloc[0].date()
    print(f"  scored on : {actual_date}")
    print(f"  dq rows   : {len(dq)}")
    print(f"  tech rows : {len(tc)}")

    N = 100
    dq_top = dq.sort_values("pred_5d", ascending=False).head(N)
    tc_top = tc.sort_values("pred_10d", ascending=False).head(N)

    overlap = set(dq_top["symbol"]) & set(tc_top["symbol"])
    print(f"\n=== Top {N} overlap: {len(overlap)}/{N} ===")

    if not overlap:
        print("No symbols hit both lists. Try a wider N or different date.")
        print(f"\n--- deliv_qty h=5 top {N} ---")
        print(dq_top[["symbol", "close", "pred_5d", "pred_5d_pctile"]].to_string(index=False))
        print(f"\n--- technical h=10 top {N} ---")
        print(tc_top[["symbol", "close", "pred_10d", "pred_10d_pctile"]].to_string(index=False))
        return

    dq_sub = dq_top[["symbol", "date", "close", "pred_5d", "pred_5d_pctile"]]
    tc_sub = tc_top[["symbol", "date", "close", "pred_10d", "pred_10d_pctile"]]
    m = dq_sub.merge(tc_sub, on=["symbol", "date", "close"])
    m = m[m["symbol"].isin(overlap)].sort_values("pred_5d", ascending=False)

    cols = ["symbol", "close", "pred_5d", "pred_5d_pctile", "pred_10d", "pred_10d_pctile"]
    print(m[cols].to_string(index=False))


if __name__ == "__main__":
    main()
