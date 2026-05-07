"""Backfill historical TTM P/E from yfinance into per-ticker storage.

Run manually:
    python backfill.py                # all tickers from config.toml
    python backfill.py AAPL MSFT      # specific tickers
    python backfill.py --days 1825    # ~5 years instead of default 1

Forward P/E cannot be backfilled (analyst consensus history isn't free), so
those fields are left null in backfilled rows. Existing snapshots are never
overwritten — only missing dates are added.

EPS source: prefers Ticker.get_earnings_dates (deeper history, real report
dates). Falls back to Ticker.quarterly_income_stmt (~5 quarters, approximate
report dates via 45-day filing-lag assumption).

Currency note: yfinance's raw EPS may be in the company's reporting currency
(e.g., TWD for TSM, CNY for BABA) while prices are in the trading currency.
We calibrate every backfilled EPS series by info.trailingEps / sum(latest 4
reported quarters) so the historical TTM matches the live-snapshot value at
the present moment. This is exact at the calibration date and approximate
elsewhere — historical FX drift introduces a few percent of error over 5y.
"""

import argparse
from datetime import timedelta

import pandas as pd
import yfinance as yf

import config
import storage


REPORTING_LAG_DAYS = 45  # Used only by the income-statement fallback


def _eps_history(ticker_obj: yf.Ticker, true_ttm_eps: float | None) -> list[tuple]:
    """Return [(available_date, scaled_eps), ...] sorted oldest-first.

    Calibrates raw values by `true_ttm_eps / sum(latest 4 reported)` so units
    match the trading currency. Tries earnings_dates first, falls back to
    quarterly_income_stmt.
    """
    # Preferred source: get_earnings_dates (real report dates, ~24 quarters)
    try:
        ed = ticker_obj.get_earnings_dates(limit=25)
        if ed is not None and not ed.empty:
            ed = ed.dropna(subset=["Reported EPS"]).sort_index()
            if len(ed) >= 4:
                raw_recent_4 = float(ed["Reported EPS"].iloc[-4:].sum())
                scalar = (
                    (true_ttm_eps / raw_recent_4)
                    if (raw_recent_4 and true_ttm_eps)
                    else 1.0
                )
                rows = []
                for ts, row in ed.iterrows():
                    d = ts.date() if hasattr(ts, "date") else ts
                    rows.append((d, float(row["Reported EPS"]) * scalar))
                return rows
    except Exception:
        pass

    # Fallback: quarterly_income_stmt (shallow, approximate report dates)
    qfin = ticker_obj.quarterly_income_stmt
    if qfin is None or qfin.empty:
        return []
    eps_row = None
    for label in ("Diluted EPS", "Basic EPS"):
        if label in qfin.index:
            eps_row = qfin.loc[label]
            break
    if eps_row is None:
        return []
    raw = []
    for col, val in eps_row.items():
        if val is None or pd.isna(val):
            continue
        qe_date = col.date() if hasattr(col, "date") else col
        raw.append((qe_date, float(val)))
    if len(raw) < 4:
        return []
    raw.sort(key=lambda x: x[0])
    raw_recent_4 = sum(eps for _, eps in raw[-4:])
    scalar = (
        (true_ttm_eps / raw_recent_4) if (raw_recent_4 and true_ttm_eps) else 1.0
    )
    lag = timedelta(days=REPORTING_LAG_DAYS)
    return [(qe + lag, eps * scalar) for qe, eps in raw]


def backfill(ticker: str, db_path: str, days: int) -> tuple[int, str]:
    """Compute historical TTM P/E and merge into storage.

    Returns (rows_added, status_message).
    """
    yt = yf.Ticker(ticker)
    info = yt.info
    name = info.get("longName", "")
    currency = info.get("currency")
    true_ttm_eps = info.get("trailingEps")

    prices = yt.history(period=f"{days}d", auto_adjust=True)
    if prices.empty:
        return 0, "no price history from yfinance"
    closes = prices["Close"]

    eps_history = _eps_history(yt, true_ttm_eps)
    if len(eps_history) < 4:
        return 0, f"only {len(eps_history)} quarter(s) of EPS available (need 4)"

    rows = []
    for ts, price in closes.items():
        d = ts.date() if hasattr(ts, "date") else ts
        applicable = [eps for rd, eps in eps_history if rd <= d]
        if len(applicable) < 4:
            continue
        ttm_eps = sum(applicable[-4:])
        ttm_pe = float(price) / ttm_eps if ttm_eps > 0 else None
        rows.append({
            "date": d.isoformat(),
            "name": name,
            "currency": currency,
            "price": float(price),
            "trailing_eps": ttm_eps,
            "forward_eps": None,
            "ttm_pe": ttm_pe,
            "forward_pe": None,
        })

    if not rows:
        return 0, "EPS history doesn't reach back far enough for any price date"

    added = storage.merge_history(db_path, ticker, rows)
    if added == 0:
        return 0, "all dates already present in storage"
    return added, "ok"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill historical TTM P/E into per-ticker storage."
    )
    parser.add_argument(
        "tickers", nargs="*", help="Tickers to backfill (default: all in config.toml)"
    )
    parser.add_argument(
        "--days", type=int, default=365, help="Days of history to attempt (default: 365)"
    )
    args = parser.parse_args()

    cfg = config.load_config()
    storage.init_db(cfg["database_path"])
    tickers = [t.upper() for t in args.tickers] or cfg["tickers"]

    print(f"Backfilling {len(tickers)} ticker(s), up to {args.days} days each...\n")
    succeeded, skipped = 0, 0
    for t in tickers:
        try:
            added, status = backfill(t, cfg["database_path"], args.days)
            if added > 0:
                print(f"  {t}: +{added} rows")
                succeeded += 1
            else:
                print(f"  {t}: skipped — {status}")
                skipped += 1
        except Exception as e:
            print(f"  {t}: failed — {e}")
            skipped += 1
    print(f"\nDone. {succeeded} succeeded, {skipped} skipped/failed.")


if __name__ == "__main__":
    main()
