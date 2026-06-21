"""Backfill historical TTM P/E into per-ticker storage.

Run manually (from pe_monitor/):
    python backfill/backfill.py             # all tickers from config.toml
    python backfill/backfill.py AAPL MSFT   # specific tickers
    python backfill/backfill.py --days 1825 # ~5 years instead of default 1

Per-ticker logic lives in the fetcher backends (see fetcher.py); this script
is just dispatch + storage. Forward P/E cannot be backfilled (analyst
consensus history isn't free), so those fields are left null in backfilled
rows. Live-snapshot values are preserved; backfill fills NULL columns of
existing rows (e.g. populating volume on dates that predate the column).
"""

import argparse

import _bootstrap  # noqa: F401  (sys.path shim)
import config
import fetcher
import storage


def backfill(ticker: str, db_path: str, days: int) -> tuple[int, int, str]:
    """Compute historical TTM P/E and merge into storage.

    Returns (inserted, filled, status). `filled` counts existing rows that
    had at least one NULL column populated by this backfill (e.g. volume
    being added to dates that predate the column).
    """
    rows, status = fetcher.get_fetcher(ticker).backfill_history(ticker, days)
    if not rows:
        return 0, 0, status
    inserted, filled = storage.merge_history(db_path, ticker, rows)
    if inserted == 0 and filled == 0:
        return 0, 0, "all dates already current"
    return inserted, filled, "ok"


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
        tag = fetcher.get_fetcher(t).__class__.__name__.removesuffix("Fetcher").lower()
        prefix = f"  {t} [{tag}]"
        try:
            inserted, filled, status = backfill(t, cfg["database_path"], args.days)
            if inserted == 0 and filled == 0:
                print(f"{prefix}: skipped — {status}")
                skipped += 1
            else:
                parts = []
                if inserted:
                    parts.append(f"+{inserted} new")
                if filled:
                    parts.append(f"+{filled} filled")
                print(f"{prefix}: {', '.join(parts)}")
                succeeded += 1
        except Exception as e:
            print(f"{prefix}: failed — {e}")
            skipped += 1
    print(f"\nDone. {succeeded} succeeded, {skipped} skipped/failed.")


if __name__ == "__main__":
    main()
