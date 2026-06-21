# Backfill tools

One-off / occasional scripts that seed historical data into `pe_history.db`.
They are **not** part of the running app (`app.py` + `scheduler.py` only ever
*append* live snapshots); keep them here so the live code stays lean while the
backfill machinery remains available for re-runs and onboarding new tickers.

All tools resolve `config.toml`, `pe_history.db`, and the IBES CSVs relative to
the parent `pe_monitor/` directory (via `config.py` and `_bootstrap.py`), so
they work regardless of where you launch them. Examples below assume you run
from `pe_monitor/`.

## Pipelines

### 1. yfinance TTM P/E history — `backfill.py`
Rebuilds daily price + trailing-twelve-month P/E from yfinance for each ticker.
Forward P/E is left NULL (analyst-consensus history isn't free here). Use this
when you add a new ticker and want its past filled in.

```
python backfill/backfill.py                 # all tickers in config.toml
python backfill/backfill.py NVDA --days 1825 # one ticker, ~5 years
```

### 2. Wayback forward P/E — `wayback_fwdpe.py` → `import_wayback_fwdpe.py`
Scrapes archived Yahoo "key statistics" pages from the Internet Archive to
recover *historical* forward P/E (Yahoo only exposes the current value). The
scraper emits JSON; the importer merges it, discarding any date on/after
`first_live_collection_date` so live snapshots stay authoritative.

```
python backfill/wayback_fwdpe.py NVDA --json | python backfill/import_wayback_fwdpe.py NVDA
python backfill/wayback_fwdpe.py --all       # batch-scrape into ./wayback_out/
python backfill/import_wayback_fwdpe.py --all # import every <TICKER>.json
```

### 3. IBES forward P/E — `import_ibes.py`
Builds monthly forward-P/E anchors (PRICE / MEANEST) from the I/B/E/S summary +
actuals dumps and writes them to `history.forward_pe_ibes`. `app.py`
interpolates these sparse anchors to a daily line at serve time. Requires the
`[ibes]` section (CSV names + ticker_map) in `config.toml`.

```
python backfill/import_ibes.py --dry-run     # report only
python backfill/import_ibes.py               # write anchors
```

Helpers:
- `check_ibes_consistency.py` — cross-checks DB forward P/E against an IBES
  consensus dump (validation only; takes `--db` / `--ibes`).
- `explore_ibes_join.ipynb` — scratch notebook used to work out the join.

## Data locations (all gitignored)
- IBES CSVs (`ibes.*.csv`, multi-GB) live in `pe_monitor/`.
- Wayback scrape output defaults to `./wayback_out/` and `wayback*.log`.
