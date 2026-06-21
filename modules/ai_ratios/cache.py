"""In-memory cache + this module's own scheduler.

Computing fetches ~500 S&P market caps from Yahoo (slow), so we compute on
startup and on an interval, serving the cached result instantly. A manual
refresh re-runs it on demand.
"""

import threading
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from . import config, core

_lock = threading.Lock()
_state: dict = {
    "computed_at": None,
    "raw": None,
    "adjusted": None,
    "rows": [],
    "missing": [],
}
_scheduler: BackgroundScheduler | None = None


def get() -> dict:
    with _lock:
        return dict(_state)


def refresh() -> dict:
    weights = core.sp500_weights()
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
        _state.update(
            computed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            raw=raw,
            adjusted=adjusted,
            rows=rows,
            missing=missing,
        )
        return dict(_state)


def start() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        refresh, "interval",
        seconds=config.REFRESH_INTERVAL_SECONDS, id="ai_ratios_refresh",
    )
    _scheduler.start()
    try:
        refresh()
    except Exception as e:
        print(f"  ai_ratios: initial compute failed ({e}); will retry on schedule")


def stop() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
