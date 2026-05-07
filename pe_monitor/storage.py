"""SQLite-backed storage for P/E history. One table keyed by (ticker, date)."""

import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    ticker        TEXT NOT NULL,
    date          TEXT NOT NULL,
    name          TEXT,
    currency      TEXT,
    price         REAL,
    trailing_eps  REAL,
    forward_eps   REAL,
    ttm_pe        REAL,
    forward_pe    REAL,
    PRIMARY KEY (ticker, date)
);
"""

ROW_COLS = (
    "date", "name", "currency", "price",
    "trailing_eps", "forward_eps", "ttm_pe", "forward_pe",
)


def init_db(db_path: str) -> None:
    """Create the database file and schema if they don't exist. Idempotent."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)


def read_history(db_path: str, ticker: str) -> list[dict]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT {', '.join(ROW_COLS)} FROM history "
            "WHERE ticker = ? ORDER BY date",
            (ticker.upper(),),
        ).fetchall()
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
                "price": None, "trailing_eps": None, "forward_eps": None,
                "ttm_pe": None, "forward_pe": None,
            })
    return result


def append_snapshot(db_path: str, ticker: str, snapshot: dict) -> None:
    """UPSERT: same-day entries are replaced (latest write wins)."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(f"""
            INSERT INTO history (ticker, {', '.join(ROW_COLS)})
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, date) DO UPDATE SET
                name=excluded.name,
                currency=excluded.currency,
                price=excluded.price,
                trailing_eps=excluded.trailing_eps,
                forward_eps=excluded.forward_eps,
                ttm_pe=excluded.ttm_pe,
                forward_pe=excluded.forward_pe
        """, (
            ticker.upper(),
            snapshot["date"], snapshot.get("name"), snapshot.get("currency"),
            snapshot.get("price"), snapshot.get("trailing_eps"),
            snapshot.get("forward_eps"), snapshot.get("ttm_pe"),
            snapshot.get("forward_pe"),
        ))


def merge_history(db_path: str, ticker: str, new_rows: list[dict]) -> int:
    """Additive merge: existing (ticker, date) rows are kept untouched.

    Backfill should fill gaps, never overwrite live snapshots (which carry
    forward_pe values that backfill can't reproduce).
    Returns the number of rows actually inserted.
    """
    if not new_rows:
        return 0
    ticker = ticker.upper()
    with sqlite3.connect(db_path) as conn:
        existing = {r[0] for r in conn.execute(
            "SELECT date FROM history WHERE ticker = ?", (ticker,)
        )}
        additions = [r for r in new_rows if r["date"] not in existing]
        if not additions:
            return 0
        conn.executemany(f"""
            INSERT INTO history (ticker, {', '.join(ROW_COLS)})
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            (ticker, r["date"], r.get("name"), r.get("currency"),
             r.get("price"), r.get("trailing_eps"), r.get("forward_eps"),
             r.get("ttm_pe"), r.get("forward_pe"))
            for r in additions
        ])
    return len(additions)
