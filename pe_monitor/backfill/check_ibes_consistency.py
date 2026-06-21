"""Cross-check pe_history.db's NVDA forward P/E against an IBES consensus dump.

For every (date, forward_pe) anchor in the DB:
  1. Get the NVDA close on that date (DB.price first, else yfinance).
  2. Implied EPS = price / forward_pe.
  3. Look up the most recent IBES MEANEST as of that date for both
     FPI=1 (current FY) and FPI=2 (next FY).
  4. Report which IBES horizon matches the implied EPS, and the ratio.

Yahoo's Key Statistics "Forward P/E" historically used the next-fiscal-year
analyst consensus (FPI=2 just before an earnings release, FPI=1 right after
it once the prior FY's number rolls off). So expect a ratio near 1.0 against
the closer of the two; sustained drift would suggest the DB values are off.
"""

import argparse
import csv
import sqlite3
import sys
from bisect import bisect_right
from collections import defaultdict


def load_ibes(path: str, ticker: str) -> dict[int, list[tuple[str, float, str]]]:
    """{fpi -> sorted list of (statpers, meanest, fpedats)}"""
    by_fpi: dict[int, list[tuple[str, float, str]]] = defaultdict(list)
    with open(path, newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            if row["OFTIC"] != ticker:
                continue
            if row["MEASURE"] != "EPS" or row["FISCALP"] != "ANN":
                continue
            try:
                fpi = int(row["FPI"])
                mean = float(row["MEANEST"])
            except (ValueError, TypeError):
                continue
            by_fpi[fpi].append((row["STATPERS"], mean, row["FPEDATS"]))
    for fpi in by_fpi:
        by_fpi[fpi].sort(key=lambda x: x[0])
    return by_fpi


def lookup_ibes(series: list[tuple[str, float, str]], d: str):
    """Most recent IBES row with statpers <= d."""
    if not series:
        return None
    keys = [s[0] for s in series]
    i = bisect_right(keys, d) - 1
    if i < 0:
        return None
    return series[i]


def get_prices(dates: list[str], ticker: str) -> dict[str, float]:
    """Fetch close prices via yfinance for the dates we lack. Note these are
    split-adjusted to the current share count."""
    import yfinance as yf
    if not dates:
        return {}
    start = min(dates)
    end_d = max(dates)
    yr, mo, day = end_d.split("-")
    end = f"{int(yr) + (1 if mo == '12' else 0)}-{(int(mo) % 12) + 1:02d}-01"
    df = yf.download(ticker, start=start, end=end, auto_adjust=False, progress=False)
    if df.empty:
        return {}
    out = {}
    for ts, row in df.iterrows():
        out[ts.date().isoformat()] = float(row["Close"].iloc[0]
                                          if hasattr(row["Close"], "iloc")
                                          else row["Close"])
    return out


def get_split_adjustments(ticker: str) -> list[tuple[str, float]]:
    """Returns [(split_date, ratio)] for the ticker. A 4:1 split has ratio 4.
    Used to scale historical IBES EPS forward into current-share space."""
    import yfinance as yf
    splits = yf.Ticker(ticker).splits
    if splits is None or splits.empty:
        return []
    return [(idx.date().isoformat(), float(val)) for idx, val in splits.items()]


def split_factor_after(splits: list[tuple[str, float]], d: str) -> float:
    """Product of all split ratios with date > d. Multiplying an EPS-as-of-d
    by 1/factor moves it into current-share basis."""
    factor = 1.0
    for sd, ratio in splits:
        if sd > d:
            factor *= ratio
    return factor


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ticker", default="NVDA")
    ap.add_argument("--ibes", default="ibes.csv")
    ap.add_argument("--db", default="pe_history.db")
    ap.add_argument("--anchors-only", action="store_true",
                    help="Only check rows with non-NULL forward_pe (skip "
                         "interpolated/live filled rows where we already trust "
                         "the implied EPS).")
    args = ap.parse_args()

    print(f"loading IBES for {args.ticker} from {args.ibes}...", file=sys.stderr)
    ibes = load_ibes(args.ibes, args.ticker)
    if not ibes:
        sys.exit(f"no IBES rows for {args.ticker}")
    print(f"  FPI=1: {len(ibes.get(1, []))} rows, "
          f"FPI=2: {len(ibes.get(2, []))} rows", file=sys.stderr)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    db_rows = conn.execute(
        "SELECT date, price, forward_pe FROM history "
        "WHERE ticker = ? AND forward_pe IS NOT NULL ORDER BY date",
        (args.ticker,),
    ).fetchall()
    conn.close()
    print(f"  DB anchors: {len(db_rows)}", file=sys.stderr)

    # Backfill missing prices via yfinance.
    missing = [r["date"] for r in db_rows if r["price"] is None]
    if missing:
        print(f"  fetching {len(missing)} prices via yfinance...", file=sys.stderr)
        ydf = get_prices(missing, args.ticker)
    else:
        ydf = {}

    splits = get_split_adjustments(args.ticker)
    if splits:
        print(f"  splits: {splits}", file=sys.stderr)

    # Find the closest trading day in ydf for each missing date.
    yf_dates_sorted = sorted(ydf)

    def yf_price_on_or_before(d: str):
        if not yf_dates_sorted:
            return None
        i = bisect_right(yf_dates_sorted, d) - 1
        if i < 0:
            return None
        return ydf[yf_dates_sorted[i]], yf_dates_sorted[i]

    print()
    print(f"{'date':<11} {'price':>9} {'fwdPE':>7} {'impEPS':>8} "
          f"{'IBES1':>7} {'r1':>5} {'IBES2':>7} {'r2':>5}  best")
    print("-" * 80)

    ratios_1, ratios_2, ratios_best = [], [], []
    ratios_best_recent = []  # post-2024 (post-10:1 split, both sides clean)
    for r in db_rows:
        d = r["date"]
        fwd_pe = r["forward_pe"]
        price = r["price"]
        price_src = "db"
        if price is None:
            res = yf_price_on_or_before(d)
            if res is None:
                print(f"{d}  -- no price available --")
                continue
            price, used_d = res
            price_src = f"yf:{used_d}"
        implied_eps = price / fwd_pe if fwd_pe else None
        if implied_eps is None:
            continue
        e1 = lookup_ibes(ibes.get(1, []), d)
        e2 = lookup_ibes(ibes.get(2, []), d)
        # IBES EPS is in share-count-of-that-FY. yfinance prices are
        # split-adjusted to current shares. Scale IBES forward by all splits
        # that occurred AFTER the IBES statpers date.
        eps1 = (e1[1] / split_factor_after(splits, e1[0])) if e1 else None
        eps2 = (e2[1] / split_factor_after(splits, e2[0])) if e2 else None
        r1 = (implied_eps / eps1) if eps1 else None
        r2 = (implied_eps / eps2) if eps2 else None
        if r1 is not None:
            ratios_1.append(r1)
        if r2 is not None:
            ratios_2.append(r2)
        # Best: whichever ratio is closest to 1.0.
        best = "?"
        best_r = None
        if r1 is not None and r2 is not None:
            if abs(r1 - 1) < abs(r2 - 1):
                best, best_r = "FY1", r1
            else:
                best, best_r = "FY2", r2
        elif r1 is not None:
            best, best_r = "FY1", r1
        elif r2 is not None:
            best, best_r = "FY2", r2
        if best_r is not None:
            ratios_best.append(best_r)
            if d <= "2025-11-20":  # while IBES dump is fresh
                ratios_best_recent.append(best_r)
        eps1_s = f"{eps1:7.3f}" if eps1 is not None else "      -"
        eps2_s = f"{eps2:7.3f}" if eps2 is not None else "      -"
        r1_s = f"{r1:5.2f}" if r1 is not None else "    -"
        r2_s = f"{r2:5.2f}" if r2 is not None else "    -"
        print(f"{d}  {price:8.2f} {fwd_pe:7.2f} {implied_eps:8.3f} "
              f"{eps1_s} {r1_s}  {eps2_s} {r2_s}  {best}  ({price_src})")

    def summarize(name, ratios):
        if not ratios:
            print(f"  {name}: no data", file=sys.stderr)
            return
        ratios_sorted = sorted(ratios)
        n = len(ratios_sorted)
        mean = sum(ratios_sorted) / n
        median = ratios_sorted[n // 2]
        within10 = sum(1 for r in ratios if 0.9 <= r <= 1.1) / n
        within25 = sum(1 for r in ratios if 0.75 <= r <= 1.25) / n
        print(f"  {name}: n={n} mean={mean:.3f} median={median:.3f} "
              f"within ±10%={within10:.0%} within ±25%={within25:.0%}",
              file=sys.stderr)

    print(file=sys.stderr)
    print("=== implied_EPS / IBES_MEANEST ratios ===", file=sys.stderr)
    summarize("vs FPI=1 (current FY)", ratios_1)
    summarize("vs FPI=2 (next FY)", ratios_2)
    summarize("best of FY1/FY2 (full range)", ratios_best)
    summarize("best of FY1/FY2 (date <= IBES dump end 2025-11-20)",
              ratios_best_recent)


if __name__ == "__main__":
    main()
