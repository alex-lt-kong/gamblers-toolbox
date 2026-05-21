"""Periodic crawler: dispatches each ticker to the right fetcher and persists."""

from apscheduler.schedulers.background import BackgroundScheduler

import fetcher
import storage


def snapshot_all(tickers: list[str], db_path: str) -> None:
    for t in tickers:
        try:
            snap = fetcher.get_fetcher(t).fetch_pe(t)
            storage.append_snapshot(db_path, t, snap)
        except Exception as e:
            print(f"  Warning: could not fetch {t}: {e}")


def start_scheduler(
    tickers: list[str], db_path: str, interval_seconds: int
) -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        snapshot_all,
        "interval",
        seconds=interval_seconds,
        args=[tickers, db_path],
        id="snapshot",
    )
    scheduler.start()
    snapshot_all(tickers, db_path)
    return scheduler
