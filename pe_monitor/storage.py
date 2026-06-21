"""SQLite-backed storage for P/E history. One table keyed by (ticker, date)."""

import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    ticker             TEXT NOT NULL,
    date               TEXT NOT NULL,
    name               TEXT,
    currency           TEXT,
    price              REAL,
    volume             INTEGER,
    trailing_eps       REAL,
    forward_eps        REAL,
    ttm_pe             REAL,
    forward_pe         REAL,
    analyst_count      INTEGER,
    financial_currency TEXT,
    forward_eps_native REAL,
    forward_pe_ibes    REAL,
    last_crawl_at      TEXT,
    PRIMARY KEY (ticker, date)
);
"""

ROW_COLS = (
    "date", "name", "currency", "price", "volume",
    "trailing_eps", "forward_eps", "ttm_pe", "forward_pe",
    "analyst_count", "financial_currency", "forward_eps_native",
    "forward_pe_ibes", "last_crawl_at",
)

_MIGRATIONS = (
    ("analyst_count",      "ALTER TABLE history ADD COLUMN analyst_count INTEGER"),
    ("financial_currency", "ALTER TABLE history ADD COLUMN financial_currency TEXT"),
    ("forward_eps_native", "ALTER TABLE history ADD COLUMN forward_eps_native REAL"),
    ("volume",             "ALTER TABLE history ADD COLUMN volume INTEGER"),
    ("forward_pe_ibes",    "ALTER TABLE history ADD COLUMN forward_pe_ibes REAL"),
    ("last_crawl_at",      "ALTER TABLE history ADD COLUMN last_crawl_at TEXT"),
)


def init_db(db_path: str) -> None:
    """Create the database file and schema if they don't exist. Idempotent.
    Also runs additive migrations for columns added after initial release."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
        existing = {r[1] for r in conn.execute("PRAGMA table_info(history)")}
        for col, ddl in _MIGRATIONS:
            if col not in existing:
                conn.execute(ddl)


def read_history(
    db_path: str,
    ticker: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict]:
    """Read history rows for a ticker, optionally bounded by [start_date,
    end_date] (inclusive). Both bounds are ISO-format date strings."""
    sql = f"SELECT {', '.join(ROW_COLS)} FROM history WHERE ticker = ?"
    params: list = [ticker.upper()]
    if start_date:
        sql += " AND date >= ?"
        params.append(start_date)
    if end_date:
        sql += " AND date <= ?"
        params.append(end_date)
    sql += " ORDER BY date"
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def latest_per_ticker(db_path: str, tickers: list[str]) -> list[dict]:
    """Return the most-recent row per requested ticker, with empty stubs for
    tickers that have no rows yet. Order matches the input `tickers` list."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(f"""
            SELECT ticker, {', '.join(ROW_COLS)} FROM history
            WHERE (ticker, date) IN (
                SELECT ticker, MAX(date) FROM history GROUP BY ticker
            )
        """).fetchall()
    by_ticker = {r["ticker"]: dict(r) for r in rows}
    result = []
    for t in tickers:
        if t in by_ticker:
            result.append(by_ticker[t])
        else:
            result.append({
                "ticker": t, "date": None, "name": "", "currency": None,
                "price": None, "volume": None,
                "trailing_eps": None, "forward_eps": None,
                "ttm_pe": None, "forward_pe": None, "analyst_count": None,
                "financial_currency": None, "forward_eps_native": None,
                "forward_pe_ibes": None, "last_crawl_at": None,
            })
    return result


def append_snapshot(db_path: str, ticker: str, snapshot: dict) -> None:
    """UPSERT for same-day entries: latest non-NULL write wins.

    The snapshot replaces any existing column where the new value is
    non-NULL, but keeps the existing value if the new value is NULL — so a
    transient missing field (e.g. yfinance returning 0/None for KR volume,
    or a network glitch dropping forwardPE) won't wipe out a previously
    good value from backfill or an earlier snapshot the same day.
    """
    placeholders = ", ".join(["?"] * (1 + len(ROW_COLS)))
    update_clause = ", ".join(
        f"{c}=COALESCE(excluded.{c}, history.{c})"
        for c in ROW_COLS if c != "date"
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(f"""
            INSERT INTO history (ticker, {', '.join(ROW_COLS)})
            VALUES ({placeholders})
            ON CONFLICT(ticker, date) DO UPDATE SET {update_clause}
        """, (ticker.upper(), snapshot["date"]) + tuple(
            snapshot.get(c) for c in ROW_COLS if c != "date"
        ))


def merge_history(
    db_path: str, ticker: str, new_rows: list[dict]
) -> tuple[int, int]:
    """Insert missing dates and fill NULL columns in existing rows.

    Existing non-NULL values win — live snapshots carry forward_pe /
    analyst_count that backfill can't reproduce, and they stay. This means
    later backfills (e.g. adding volume after the fact) populate the new
    column on historical rows without overwriting anything already there.
    Returns (inserted, filled): dates newly inserted, and dates already
    present that had at least one NULL column filled in.
    """
    if not new_rows:
        return 0, 0
    ticker = ticker.upper()
    placeholders = ", ".join(["?"] * (1 + len(ROW_COLS)))
    fillable_cols = tuple(c for c in ROW_COLS if c != "date")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        existing_rows = {
            r["date"]: dict(r) for r in conn.execute(
                f"SELECT {', '.join(ROW_COLS)} FROM history WHERE ticker = ?",
                (ticker,),
            )
        }
        inserts, fillable = [], []
        for r in new_rows:
            ex = existing_rows.get(r["date"])
            if ex is None:
                inserts.append(r)
            elif any(ex.get(c) is None and r.get(c) is not None for c in fillable_cols):
                fillable.append(r)
        if inserts:
            conn.executemany(
                f"INSERT INTO history (ticker, {', '.join(ROW_COLS)}) "
                f"VALUES ({placeholders})",
                [(ticker,) + tuple(r.get(c) for c in ROW_COLS) for r in inserts],
            )
        if fillable:
            update_clause = ", ".join(
                f"{c}=COALESCE(history.{c}, excluded.{c})" for c in fillable_cols
            )
            conn.executemany(
                f"INSERT INTO history (ticker, {', '.join(ROW_COLS)}) "
                f"VALUES ({placeholders}) "
                f"ON CONFLICT(ticker, date) DO UPDATE SET {update_clause}",
                [(ticker,) + tuple(r.get(c) for c in ROW_COLS) for r in fillable],
            )
    return len(inserts), len(fillable)
