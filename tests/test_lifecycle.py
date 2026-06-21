from fastapi.testclient import TestClient

import modules.ai_ratios.cache as cache
import modules.pe_monitor.scheduler as sched
from modules.pe_monitor import views as pe_views


def test_lifespan_starts_and_stops(make_app, monkeypatch):
    # keep startup hermetic: no real network in the one-off initial jobs
    monkeypatch.setattr(cache, "_scheduled_refresh", lambda: None)
    monkeypatch.setattr(sched, "_snapshot_safe", lambda *a, **k: None)
    with TestClient(make_app()) as c:        # runs lifespan startup
        assert c.get("/").status_code == 200
    # context exit ran shutdown: both module schedulers were stopped
    assert cache._scheduler is None
    assert pe_views._scheduler is None
