from datetime import date

import pytest
from fastapi.testclient import TestClient

from modules.crypto_tracker import cache, twr

# --- Pure return math (prices injected; no network) ---

TODAY = "2024-01-01"


def _single_deposit():
    rows = [{"date": "2023-01-01", "asset": "BTC", "delta": 1.0, "note": ""}]
    return rows, ["BTC"], {"BTCUSDT:2023-01-01": 100.0}, {"BTCUSDT": 200.0}


def test_twr_single_deposit_doubles():
    rows, assets, price_cache, today = _single_deposit()
    twr_v = twr.twr_over_range(rows, assets, "2023-01-01", TODAY, TODAY, today, price_cache)
    assert twr_v == pytest.approx(1.0)  # 100 -> 200


def test_mwr_equals_twr_without_interior_flows():
    rows, assets, price_cache, today = _single_deposit()
    twr_v = twr.twr_over_range(rows, assets, "2023-01-01", TODAY, TODAY, today, price_cache)
    mwr_v = twr.mwr_over_range(rows, assets, "2023-01-01", TODAY, TODAY, today, price_cache)
    assert mwr_v == pytest.approx(twr_v, abs=1e-6)


def test_twr_strips_timing_but_mwr_rewards_the_dip_buy():
    # Price 100 -> 50 -> 100 (flat over time) but a second buy lands at the dip.
    rows = [
        {"date": "2023-01-01", "asset": "BTC", "delta": 1.0, "note": ""},
        {"date": "2023-07-01", "asset": "BTC", "delta": 1.0, "note": "dip buy"},
    ]
    price_cache = {"BTCUSDT:2023-01-01": 100.0, "BTCUSDT:2023-07-01": 50.0}
    today = {"BTCUSDT": 100.0}
    twr_v = twr.twr_over_range(rows, ["BTC"], "2023-01-01", TODAY, TODAY, today, price_cache)
    mwr_v = twr.mwr_over_range(rows, ["BTC"], "2023-01-01", TODAY, TODAY, today, price_cache)
    assert twr_v == pytest.approx(0.0, abs=1e-9)  # time-weighted: round trip = flat
    assert mwr_v is not None and mwr_v > 0  # buying the dip earns a positive IRR


def test_xirr_recovers_known_rate():
    flows = [(date(2023, 1, 1), -100.0), (date(2024, 1, 1), 110.0)]
    assert twr.xirr(flows) == pytest.approx(0.10, abs=1e-4)


def test_xirr_none_without_sign_change():
    assert twr.xirr([(date(2023, 1, 1), -1.0), (date(2024, 1, 1), -2.0)]) is None


@pytest.mark.parametrize("cumul,days,expected", [
    (0.5, 100, None),    # under a year -> not annualized
    (1.0, 365, 1.0),     # one year -> CAGR == cumulative
    (-1.5, 400, -1.0),   # worse than total loss is clamped
    (None, 400, None),
])
def test_annualize_cumul(cumul, days, expected):
    got = twr.annualize_cumul(cumul, days)
    assert got is None if expected is None else got == pytest.approx(expected)


def test_balance_at_is_cumulative_per_asset():
    rows = [
        {"date": "2023-01-01", "asset": "BTC", "delta": 1.0, "note": ""},
        {"date": "2023-07-01", "asset": "BTC", "delta": 0.5, "note": ""},
        {"date": "2023-07-01", "asset": "ETH", "delta": 3.0, "note": ""},
    ]
    assert twr.balance_at(rows, "2023-03-01", "BTC") == pytest.approx(1.0)
    assert twr.balance_at(rows, "2023-08-01", "BTC") == pytest.approx(1.5)
    assert twr.balance_at(rows, "2023-08-01", "ETH") == pytest.approx(3.0)


def test_unknown_asset_rejected():
    with pytest.raises(ValueError):
        twr.symbol_for("DOGE")


# --- API surface (cache empty: no lifespan -> no scheduler -> no network) ---

@pytest.fixture
def restore_state():
    snap = dict(cache._state)
    yield
    cache._state.clear()
    cache._state.update(snap)


def test_page_loads(make_app):
    assert TestClient(make_app()).get("/crypto-tracker/").status_code == 200


def test_api_data_shape_when_empty(make_app):
    r = TestClient(make_app()).get("/crypto-tracker/api/data")
    assert r.status_code == 200
    body = r.json()
    assert body["holdings"] == [] and body["computed_at"] is None
    assert {"total_value", "ranges", "stale"} <= body.keys()


def test_dashboard_renders_populated_state(make_app, restore_state):
    cache._state.update(
        computed_at="2024-01-01T00:00:00Z", as_of="2024-01-01", total_value=300.0,
        holdings=[{"asset": "BTC", "qty": 1.0, "price": 200.0, "value": 200.0}],
        ranges=[
            {"name": "All-time", "twr": 0.5, "mwr": 0.6, "cagr": 0.5},
            {"name": "30D", "twr": None, "mwr": None, "cagr": None},
        ],
        stale=False, last_error=None,
    )
    html = TestClient(make_app()).get("/crypto-tracker/").text
    assert "BTC" in html and "$200.00" in html and "$300.00" in html
    assert "+50.00%" in html and "All-time" in html and "n/a" in html


def test_refresh_409_when_busy(make_app):
    c = TestClient(make_app())
    cache._refresh_lock.acquire()
    try:
        assert c.post("/crypto-tracker/api/refresh").status_code == 409
    finally:
        cache._refresh_lock.release()


def test_refresh_publishes_compute_result(make_app, monkeypatch, restore_state):
    fake = {
        "computed_at": "2024-01-01T00:00:00Z", "as_of": "2024-01-01", "total_value": 300.0,
        "holdings": [{"asset": "BTC", "qty": 1.0, "price": 200.0, "value": 200.0}],
        "ranges": [{"name": "All-time", "twr": 0.5, "mwr": 0.6, "cagr": 0.5}],
    }
    monkeypatch.setattr(cache.twr, "compute", lambda: fake)
    r = TestClient(make_app()).post("/crypto-tracker/api/refresh")
    assert r.status_code == 200
    body = r.json()
    assert body["total_value"] == 300.0 and body["stale"] is False and body["last_error"] is None


def test_refresh_keeps_last_good_on_failure(make_app, monkeypatch, restore_state):
    def boom():
        raise RuntimeError("binance down")
    monkeypatch.setattr(cache.twr, "compute", boom)
    body = TestClient(make_app()).post("/crypto-tracker/api/refresh").json()
    assert body["stale"] is True and "binance down" in body["last_error"]
