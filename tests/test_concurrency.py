from fastapi.testclient import TestClient

import modules.ai_ratios.cache as cache
import modules.pe_monitor.scheduler as sched


def test_ai_ratios_refresh_409_when_busy(make_app):
    c = TestClient(make_app())  # auth off
    cache._refresh_lock.acquire()
    try:
        assert c.post("/ai-ratios/api/refresh").status_code == 409
    finally:
        cache._refresh_lock.release()


def test_pe_monitor_refresh_409_when_busy(make_app):
    c = TestClient(make_app())
    sched._snapshot_lock.acquire()
    try:
        assert c.post("/pe-monitor/api/refresh").status_code == 409
    finally:
        sched._snapshot_lock.release()
