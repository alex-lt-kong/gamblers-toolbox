"""Backfill history.forward_pe_ibes from IBES summary + actuals dumps.

For each ticker we build a *monthly* forward-P/E anchor at every IBES statistical
period (STATPERS):

    anchor_pe = PRICE / MEANEST(FPI=2)

with both PRICE (from the actuals+price file) and MEANEST (the next-fiscal-year
consensus, from the summary-statistics file) taken from IBES at the *same*
STATPERS. Because numerator and denominator come from the same IBES row they
share currency and split basis, so the ratio is a clean, dimensionless P/E —
**no FX conversion, no split adjustment, no ADR ratio needed** (an ADR ratio
would cancel in the ratio anyway; that whole problem disappears once price and
EPS come from one source).

We anchor on FPI=2 (next fiscal year) because that is the consensus Yahoo's
forward P/E uses, so the IBES line overlays Yahoo's directly.

Anchors are written sparsely, at (or near) their STATPERS dates; app.py
interpolates them to a daily line against real yfinance prices exactly the way
it already does for the live forward_pe. So this script makes NO network calls
and stores only genuine IBES observations — the daily fill is a presentation
concern handled at serve time.

Usage (from pe_monitor/):
    python backfill/import_ibes.py             # backfill all mapped tickers
    python backfill/import_ibes.py --dry-run   # report only, write nothing
    python backfill/import_ibes.py --tickers NVDA,BABA,0700.HK
"""

import argparse
import bisect
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pandas as pd

import _bootstrap  # noqa: F401  (sys.path shim)
import config
import storage

# Estimate slice we anchor on. ANN = annual fiscal period; FPI=2 = next fiscal
# year (Yahoo's forward-P/E convention).
FISCALP = "ANN"
FPI = "2"

# How far an IBES STATPERS may be snapped to find a price-bearing history row.
# STATPERS is a monthly Thursday; this only matters when that Thursday is a
# market holiday or just outside the daily-price coverage window.
SNAP_TOLERANCE_DAYS = 7

# Warn if a ticker's most recent anchor is older than this — usually means the
# IBES code maps to a stale/delisted entity rather than the name you want.
STALE_AFTER_DAYS = 400


def load_ibes(path: str, codes: set[str], chunksize: int = 1_000_000) -> pd.DataFrame:
    """Stream a (large) IBES CSV, keeping only rows whose TICKER is in `codes`.
    Read as str so the parser never guesses dtypes; callers coerce as needed.
    pandas respects CSV quoting, so commas inside CNAME don't corrupt columns."""
    keep = []
    for chunk in pd.read_csv(path, dtype=str, chunksize=chunksize,
                             na_filter=False, low_memory=False):
        keep.append(chunk[chunk["TICKER"].isin(codes)])
    return pd.concat(keep, ignore_index=True)


def build_anchors(stat_csv: str, act_csv: str, codes: set[str]) -> pd.DataFrame:
    """One anchor row per (IBES TICKER, STATPERS): TICKER, STATPERS, anchor_pe,
    est_cc, price_cc.

    Picks the currency-consistent estimate cut when a ticker carries several,
    and drops the duplicate FY0A rows the actuals file emits per STATPERS."""
    stat = load_ibes(stat_csv, codes)
    act = load_ibes(act_csv, codes)

    fy2 = stat[(stat["FISCALP"] == FISCALP) & (stat["FPI"] == FPI)].copy()
    fy2["MEANEST"] = pd.to_numeric(fy2["MEANEST"], errors="coerce")
    fy2 = fy2[["TICKER", "STATPERS", "CURCODE", "MEANEST"]].rename(
        columns={"CURCODE": "est_cc"})

    act["PRICE"] = pd.to_numeric(act["PRICE"], errors="coerce")
    # actpsumu carries 2-3 rows per (TICKER, STATPERS) differing only in FY0A;
    # PRICE is identical across them, so collapse to one.
    act = (act[["TICKER", "STATPERS", "PRICE", "CURR_PRICE"]]
           .drop_duplicates(["TICKER", "STATPERS"]))

    j = fy2.merge(act, on=["TICKER", "STATPERS"], how="inner")
    j["cc_match"] = j["est_cc"] == j["CURR_PRICE"]
    # Prefer the currency-consistent cut, then keep one row per key.
    j = (j.sort_values(["TICKER", "STATPERS", "cc_match"],
                       ascending=[True, True, False])
           .drop_duplicates(["TICKER", "STATPERS"], keep="first"))

    j = j.dropna(subset=["PRICE", "MEANEST"])
    j = j[j["MEANEST"] != 0]
    j["anchor_pe"] = j["PRICE"] / j["MEANEST"]
    return j.rename(columns={"CURR_PRICE": "price_cc"})[
        ["TICKER", "STATPERS", "anchor_pe", "est_cc", "price_cc"]]


def price_dates(db_path: str, ticker: str) -> list[str]:
    """Sorted list of dates on which `ticker` has a non-NULL price."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT date FROM history WHERE ticker = ? AND price IS NOT NULL "
            "ORDER BY date", (ticker.upper(),)).fetchall()
    return [r[0] for r in rows]


def snap(statpers: str, dates: list[str], tol_days: int) -> str | None:
    """Nearest date in `dates` to `statpers` within `tol_days`, else None."""
    if not dates:
        return None
    i = bisect.bisect_left(dates, statpers)
    target = date.fromisoformat(statpers)
    best, best_gap = None, None
    for c in (dates[i] if i < len(dates) else None,
              dates[i - 1] if i > 0 else None):
        if c is None:
            continue
        gap = abs((date.fromisoformat(c) - target).days)
        if gap <= tol_days and (best_gap is None or gap < best_gap):
            best, best_gap = c, gap
    return best


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Backfill forward_pe_ibes from IBES dumps.")
    ap.add_argument("--dry-run", action="store_true",
                    help="report only; write nothing to the database")
    ap.add_argument("--tickers", default="",
                    help="comma-separated Yahoo tickers to limit to (default: all mapped)")
    args = ap.parse_args()

    cfg = config.load_config()
    db_path = cfg["database_path"]
    ibes = cfg.get("ibes")
    if not ibes or "ticker_map" not in ibes:
        print("No [ibes] section / ticker_map in config.toml.", file=sys.stderr)
        return 1

    pe_dir = Path(__file__).resolve().parent.parent  # CSVs live in pe_monitor/
    stat_csv = str(pe_dir / ibes["stat_csv"])
    act_csv = str(pe_dir / ibes["act_csv"])
    ticker_map: dict[str, str] = dict(ibes["ticker_map"])

    only = {t.strip().upper() for t in args.tickers.split(",") if t.strip()}
    if only:
        ticker_map = {y: c for y, c in ticker_map.items() if y.upper() in only}
        if not ticker_map:
            print(f"None of --tickers {sorted(only)} are in the ticker_map.",
                  file=sys.stderr)
            return 1

    storage.init_db(db_path)

    codes = set(ticker_map.values())
    print(f"Loading IBES dumps (filtered to {len(codes)} codes)…")
    anchors = build_anchors(stat_csv, act_csv, codes)
    by_code = {code: g for code, g in anchors.groupby("TICKER")}

    today = date.today()
    print(f"\n{'ticker':>10}  {'anchors':>7}  {'written':>7}  "
          f"{'span':>23}  {'last P/E':>8}")
    print("-" * 72)
    grand_written = 0
    for yahoo, code in sorted(ticker_map.items()):
        g = by_code.get(code)
        if g is None or g.empty:
            print(f"{yahoo:>10}  {0:>7}  {'-':>7}  {'(no IBES rows)':>23}")
            continue
        g = g.sort_values("STATPERS")
        dates = price_dates(db_path, yahoo)

        # later STATPERS wins if two anchors snap to the same trading day
        rows_by_date: dict[str, float] = {}
        for _, a in g.iterrows():
            d = snap(a["STATPERS"], dates, SNAP_TOLERANCE_DAYS)
            if d is not None:
                rows_by_date[d] = float(a["anchor_pe"])
        new_rows = [{"date": d, "forward_pe_ibes": pe}
                    for d, pe in sorted(rows_by_date.items())]

        last_statpers = g["STATPERS"].iloc[-1]
        last_pe = float(g["anchor_pe"].iloc[-1])
        stale = (today - date.fromisoformat(last_statpers)).days > STALE_AFTER_DAYS
        flag = "  ⚠ stale" if stale else ""
        span = (f"{new_rows[0]['date']}..{new_rows[-1]['date']}"
                if new_rows else "(no price overlap)")

        if not args.dry_run and new_rows:
            ins, fill = storage.merge_history(db_path, yahoo, new_rows)
            grand_written += ins + fill
        print(f"{yahoo:>10}  {len(g):>7}  {len(new_rows):>7}  "
              f"{span:>23}  {last_pe:>8.2f}{flag}")

    mode = ("DRY-RUN — nothing written" if args.dry_run
            else f"wrote/filled {grand_written} rows")
    print(f"\n{mode}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
