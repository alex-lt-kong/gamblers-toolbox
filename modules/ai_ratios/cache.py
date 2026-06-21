"""In-memory cache + this module's own scheduler.

Computing fetches ~500 S&P market caps from Yahoo (slow), so we compute on a
schedule and serve the cached result instantly. Refreshes are single-flight, and
a low-coverage pull never overwrites a good result.
"""

import threading
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from . import config, core

COVERAGE_MIN = 0.95

_lock = threading.Lock()
_refresh_lock = threading.Lock()
_state: dict = {
    "computed_at": None,
    "raw": None,
    "adjusted": None,
    "rows": [],
    "missing": [],
    "coverage": None,
    "stale": False,
    "last_error": None,
}
_scheduler: BackgroundScheduler | None = None


class Busy(Exception):
    """A refresh is already in progress."""


def get() -> dict:
    with _lock:
        return dict(_state)


def refresh() -> dict:
    if not _refresh_lock.acquire(blocking=False):
        raise Busy()
    try:
        weights, n_total, n_ok = core.sp500_weights()
        coverage = n_ok / n_total if n_total else 0.0
        raw, adjusted, missing = core.index_share(config.AI_TICKERS, weights)
        rows = sorted(
            (
                {
                    "ticker": t,
                    "fineness": fineness,
                    "weight": weights.get(t, 0.0),
                    "contribution": weights.get(t, 0.0) * fineness,
                }
                for t, fineness in config.AI_TICKERS.items()
            ),
            key=lambda r: r["contribution"],
            reverse=True,
        )
        with _lock:
            if coverage < COVERAGE_MIN and _state["rows"]:
                # keep the last known-good result rather than publish a thin one
                _state["last_error"] = (
                    f"kept previous result: coverage {coverage:.0%} < {COVERAGE_MIN:.0%}"
                )
                return dict(_state)
            _state.update(
                computed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                raw=raw,
                adjusted=adjusted,
                rows=rows,
                missing=missing,
                coverage=coverage,
                stale=coverage < COVERAGE_MIN,
                last_error=(
                    None if coverage >= COVERAGE_MIN
                    else f"low coverage: {coverage:.0%} of constituents"
                ),
            )
            return dict(_state)
    finally:
        _refresh_lock.release()


def _scheduled_refresh() -> None:
    try:
        refresh()
    except Busy:
        pass
    except Exception as e:
        print(f"  ai_ratios: scheduled refresh failed ({e})")


def start() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        _scheduled_refresh, "interval",
        seconds=config.REFRESH_INTERVAL_SECONDS, id="ai_ratios_refresh",
    )
    _scheduler.add_job(_scheduled_refresh, id="ai_ratios_initial")  # one-off, ASAP
    _scheduler.start()


def stop() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
