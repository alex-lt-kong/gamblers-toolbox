"""In-memory cache + this module's scheduler.

Computing fetches live + historical Binance prices, so we refresh on a schedule
and serve the cached result instantly. Refreshes are single-flight; a failed
refresh keeps the last known-good result and records the error.
"""

import threading
from contextlib import contextmanager

from apscheduler.schedulers.background import BackgroundScheduler

from . import config, twr

_lock = threading.Lock()
_refresh_lock = threading.Lock()
_state: dict = {
    "computed_at": None,
    "as_of": None,
    "total_value": None,
    "holdings": [],
    "ranges": [],
    "stale": True,
    "last_error": None,
}


class Busy(Exception):
    """A refresh is already in progress."""


def get() -> dict:
    with _lock:
        return dict(_state)


def refresh() -> dict:
    if not _refresh_lock.acquire(blocking=False):
        raise Busy()
    try:
        result = twr.compute()
        with _lock:
            _state.update(result, stale=False, last_error=None)
            return dict(_state)
    except Exception as e:
        with _lock:
            _state.update(stale=True, last_error=f"refresh failed: {e}")
            return dict(_state)
    finally:
        _refresh_lock.release()


def _scheduled_refresh() -> None:
    try:
        refresh()
    except Busy:
        pass
    except Exception as e:
        print(f"  crypto_tracker: scheduled refresh failed ({e})")


@contextmanager
def scheduler_lifespan():
    sched = BackgroundScheduler()
    sched.add_job(
        _scheduled_refresh, "interval",
        seconds=config.REFRESH_INTERVAL_SECONDS, id="crypto_tracker_refresh",
    )
    sched.add_job(_scheduled_refresh, id="crypto_tracker_initial")  # one-off, ASAP
    sched.start()
    try:
        yield
    finally:
        sched.shutdown(wait=False)
