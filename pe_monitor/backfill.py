"""Backfill historical TTM P/E into per-ticker storage.

Run manually:
    python backfill.py                # all tickers from config.toml
    python backfill.py AAPL MSFT      # specific tickers
    python backfill.py --days 1825    # ~5 years instead of default 1

Per-ticker logic lives in the fetcher backends (see fetcher.py); this script
is just dispatch + storage. Forward P/E cannot be backfilled (analyst
consensus history isn't free), so those fields are left null in backfilled
rows. Existing snapshots are never overwritten — only missing dates are added.
"""

import argparse

import config
import fetcher
import storage


def backfill(ticker: str, db_path: str, days: int) -> tuple[int, str]:
    """Compute historical TTM P/E and merge into storage.

    Returns (rows_added, status_message).
    """
    rows, status = fetcher.get_fetcher(ticker).backfill_history(ticker, days)
    if not rows:
        return 0, status
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
