"""Periodic crawler: dispatches each ticker to the right fetcher and persists.

Snapshots are single-flight — a scheduled tick overlapping a running snapshot is
skipped; a manual refresh that overlaps raises Busy (surfaced as HTTP 409).
"""

import threading

from apscheduler.schedulers.background import BackgroundScheduler

from . import fetcher, storage

_snapshot_lock = threading.Lock()


class Busy(Exception):
    """A snapshot is already in progress."""


def _has_usable_data(snap: dict) -> bool:
    price = snap.get("price")
    return price is not None and price > 0


def snapshot_all(tickers: list[str], db_path: str) -> None:
    if not _snapshot_lock.acquire(blocking=False):
        raise Busy()
    try:
        for t in tickers:
            try:
                snap = fetcher.get_fetcher(t).fetch_pe(t)
                if _has_usable_data(snap):
                    storage.append_snapshot(db_path, t, snap)
                else:
                    print(f"  Skipping {t}: no usable price in response")
            except Exception as e:
                print(f"  Warning: could not fetch {t}: {e}")
    finally:
        _snapshot_lock.release()


def _snapshot_safe(tickers: list[str], db_path: str) -> None:
    try:
        snapshot_all(tickers, db_path)
    except Busy:
        print("  pe_monitor: snapshot already running; skipping tick")
    except Exception as e:
        print(f"  pe_monitor: snapshot failed: {e}")


def start_scheduler(
    tickers: list[str], db_path: str, interval_seconds: int
) -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        _snapshot_safe, "interval",
        seconds=interval_seconds, args=[tickers, db_path], id="snapshot",
    )
    scheduler.add_job(_snapshot_safe, args=[tickers, db_path], id="snapshot_initial")
    scheduler.start()
    return scheduler
