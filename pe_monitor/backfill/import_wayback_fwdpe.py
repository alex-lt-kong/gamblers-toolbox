"""One-shot importer for Wayback-scraped historical Forward P/E.

Takes a ticker plus the JSON output of `wayback_fwdpe.py --json`, and merges
the rows into the history table. Two safety gates:

1. The `first_live_collection_date` config — any scraped date on or after
   this is discarded. Live daily snapshots are authoritative for that era;
   Wayback values are approximations and shouldn't trample them.
2. `storage.merge_history` uses `COALESCE(history, excluded)`, so even if a
   scraped value slipped past gate (1), an existing non-NULL forward_pe in
   the DB still wins. Belt-and-suspenders.

Usage (from pe_monitor/):
    python backfill/wayback_fwdpe.py NVDA --json > nvda.json
    python backfill/import_wayback_fwdpe.py NVDA --file nvda.json

Or pipe directly:
    python backfill/wayback_fwdpe.py NVDA --json | python backfill/import_wayback_fwdpe.py NVDA
"""

import argparse
import json
import pathlib
import sys

import _bootstrap  # noqa: F401  (sys.path shim)
import config
import storage


def load_rows(path: str | None) -> list[dict]:
    src = open(path) if path else sys.stdin
    try:
        data = json.load(src)
    finally:
        if path:
            src.close()
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON list, got {type(data).__name__}")
    return data


def import_one(
    ticker: str, rows: list[dict], cutoff: str, db_path: str, dry_run: bool
) -> tuple[int, int, int, int]:
    """Returns (kept, post_cutoff_skipped, malformed, inserted_or_filled_total).
    In dry-run mode the last element is 0."""
    pre, post, bad = [], 0, 0
    for r in rows:
        d = r.get("date")
        v = r.get("forward_pe")
        if not d or v is None:
            bad += 1
            continue
        if d >= cutoff:
            post += 1
            continue
        pre.append({
            "date": d, "name": None, "currency": None, "price": None,
            "volume": None, "trailing_eps": None, "forward_eps": None,
            "ttm_pe": None, "forward_pe": float(v), "analyst_count": None,
            "financial_currency": None, "forward_eps_native": None,
        })

    print(f"[{ticker}] {len(rows)} scraped rows; "
          f"{len(pre)} pre-cutoff (will import), "
          f"{post} on/after {cutoff} (skipped), "
          f"{bad} malformed", file=sys.stderr)

    if dry_run or not pre:
        return len(pre), post, bad, 0

    inserted, filled = storage.merge_history(db_path, ticker, pre)
    print(f"[{ticker}] +{inserted} new rows, +{filled} existing rows filled",
          file=sys.stderr)
    return len(pre), post, bad, inserted + filled


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ticker", nargs="?",
                    help="Single ticker to import (omit when using --all)")
    ap.add_argument("--file", help="JSON file from wayback_fwdpe.py (default: stdin)")
    ap.add_argument("--all", action="store_true",
                    help="Batch mode: import every <TICKER>.json file in --dir")
    ap.add_argument("--dir", default="wayback_out",
                    help="Input directory for --all mode (default: ./wayback_out)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would be written without touching the DB")
    args = ap.parse_args()

    if args.all and args.ticker:
        ap.error("pass either a ticker positional or --all, not both")
    if not args.all and not args.ticker:
        ap.error("pass a ticker, or use --all to import every file in --dir")

    cfg = config.load_config()
    cutoff = cfg.get("first_live_collection_date")
    if not cutoff:
        sys.exit("config.toml is missing `first_live_collection_date`; refusing "
                 "to import without knowing where the live era starts")
    if not args.dry_run:
        storage.init_db(cfg["database_path"])

    if not args.all:
        ticker = args.ticker.upper()
        rows = load_rows(args.file)
        kept, _, _, _ = import_one(ticker, rows, cutoff,
                                   cfg["database_path"], args.dry_run)
        if args.dry_run:
            # Replay the kept-list so the user can inspect it.
            pre = [r for r in rows if r.get("date") and r.get("date") < cutoff
                   and r.get("forward_pe") is not None]
            print(json.dumps(pre, indent=2))
        return

    # --all mode
    in_dir = pathlib.Path(args.dir).resolve()
    if not in_dir.is_dir():
        sys.exit(f"--dir {in_dir} does not exist")

    valid_tickers = {t.upper() for t in cfg["tickers"]}
    total_kept = total_changes = total_files = 0
    unknown_files = []
    for path in sorted(in_dir.glob("*.json")):
        ticker = path.stem.upper()
        if ticker not in valid_tickers:
            unknown_files.append(path.name)
            continue
        try:
            rows = load_rows(str(path))
        except Exception as e:
            print(f"[{ticker}] ERR loading {path.name}: {e}", file=sys.stderr)
            continue
        kept, _, _, changes = import_one(
            ticker, rows, cutoff, cfg["database_path"], args.dry_run
        )
        total_kept += kept
        total_changes += changes
        total_files += 1

    print(f"\n=== Summary ===", file=sys.stderr)
    print(f"  Files imported: {total_files}", file=sys.stderr)
    print(f"  Rows kept (pre-cutoff): {total_kept}", file=sys.stderr)
    print(f"  DB changes (insert+fill): {total_changes}", file=sys.stderr)
    if unknown_files:
        print(f"  Skipped (filename not in config.tickers): {unknown_files}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
