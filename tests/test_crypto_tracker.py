from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from modules.crypto_tracker import cache, twr


class _Resp:
    """Minimal stand-in for a requests Response."""

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _day_ms(on_date):
    return int(datetime.strptime(on_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)

# --- Pure return math (prices injected; no network) ---

TODAY = "2024-01-01"


class FakePrices:
    """Deterministic provider keyed by (symbol, 'YYYY-MM-DD')."""

    def __init__(self, table):
        self.table = table

    def price(self, symbol, on_date):
        return self.table[(symbol, on_date)]


def _single_deposit():
    rows = [{"date": "2023-01-01", "asset": "BTC", "delta": 1.0, "note": ""}]
    prices = FakePrices({("BTCUSDT", "2023-01-01"): 100.0, ("BTCUSDT", TODAY): 200.0})
    return rows, ["BTC"], prices


def test_twr_single_deposit_doubles():
    rows, assets, prices = _single_deposit()
    assert twr.twr_over_range(rows, assets, "2023-01-01", TODAY, prices) == pytest.approx(1.0)


def test_mwr_equals_twr_without_interior_flows():
    rows, assets, prices = _single_deposit()
    twr_v = twr.twr_over_range(rows, assets, "2023-01-01", TODAY, prices)
    mwr_v = twr.mwr_over_range(rows, assets, "2023-01-01", TODAY, prices)
    assert mwr_v == pytest.approx(twr_v, abs=1e-6)


def test_twr_strips_timing_but_mwr_rewards_the_dip_buy():
    # Price 100 -> 50 -> 100 (flat over time) but a second buy lands at the dip.
    rows = [
        {"date": "2023-01-01", "asset": "BTC", "delta": 1.0, "note": ""},
        {"date": "2023-07-01", "asset": "BTC", "delta": 1.0, "note": "dip buy"},
    ]
    prices = FakePrices({
        ("BTCUSDT", "2023-01-01"): 100.0,
        ("BTCUSDT", "2023-07-01"): 50.0,
        ("BTCUSDT", TODAY): 100.0,
    })
    twr_v = twr.twr_over_range(rows, ["BTC"], "2023-01-01", TODAY, prices)
    mwr_v = twr.mwr_over_range(rows, ["BTC"], "2023-01-01", TODAY, prices)
    assert twr_v == pytest.approx(0.0, abs=1e-9)  # time-weighted: round trip = flat
    assert mwr_v is not None and mwr_v > 0  # buying the dip earns a positive IRR


def test_short_window_large_gain_resolves_mwr():
    # Regression: a 30-day double implies an annual IRR ~4597, which overflowed
    # the old fixed high=1000 bracket -> MWR came back n/a. Now it resolves.
    rows = [{"date": "2024-01-01", "asset": "BTC", "delta": 1.0, "note": ""}]
    prices = FakePrices({("BTCUSDT", "2024-01-01"): 100.0, ("BTCUSDT", "2024-01-31"): 200.0})
    mwr_v = twr.mwr_over_range(rows, ["BTC"], "2024-01-01", "2024-01-31", prices)
    assert mwr_v is not None and mwr_v == pytest.approx(1.0, abs=1e-3)  # 100% cumulative


def test_xirr_resolves_extreme_short_window_rate():
    flows = [(date(2024, 1, 1), -100.0), (date(2024, 1, 31), 200.0)]
    r = twr.xirr(flows)
    assert r is not None and r == pytest.approx(2 ** (365 / 30) - 1, rel=1e-3)


def test_xirr_resolves_near_total_loss():
    # >99.99% loss: root sits below the old fixed lower bound, used to return None.
    flows = [(date(2023, 1, 1), -100.0), (date(2024, 1, 1), 0.001)]
    r = twr.xirr(flows)
    assert r is not None and r < -0.999


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


# --- CSV validation (reject malformed non-blank rows with line numbers) ---

def _write_csv(monkeypatch, tmp_path, body):
    p = tmp_path / "portfolio.csv"
    p.write_text(body)
    monkeypatch.setattr(twr, "PORTFOLIO_CSV", p)
    return p


def test_load_portfolio_blank_lines_skipped(monkeypatch, tmp_path):
    _write_csv(monkeypatch, tmp_path, "date,asset,delta,note\n2024-01-01,BTC,1.0,seed\n\n")
    assert len(twr.load_portfolio()) == 1


@pytest.mark.parametrize("bad,needle", [
    ("2024-13-45,BTC,1.0,x", "invalid date"),     # passes the regex shape, not a real date
    ("2024-01-01,DOGE,1.0,x", "unknown asset"),
    ("2024-01-01,BTC,abc,x", "non-numeric delta"),
    ("2024-01-01,BTC,nan,x", "non-finite delta"),
    ("2024-01-01,BTC,inf,x", "non-finite delta"),
])
def test_load_portfolio_rejects_bad_row_with_line_number(monkeypatch, tmp_path, bad, needle):
    _write_csv(monkeypatch, tmp_path, f"date,asset,delta,note\n2024-01-01,BTC,1.0,ok\n{bad}\n")
    with pytest.raises(ValueError) as e:
        twr.load_portfolio()
    assert needle in str(e.value) and "line 3" in str(e.value)


def test_load_portfolio_rejects_future_date(monkeypatch, tmp_path):
    _write_csv(monkeypatch, tmp_path, "date,asset,delta,note\n2024-01-01,BTC,1.0,ok\n2999-01-01,BTC,1.0,typo\n")
    with pytest.raises(ValueError) as e:
        twr.load_portfolio()
    assert "future date" in str(e.value) and "line 3" in str(e.value)


def test_load_portfolio_rejects_oversell(monkeypatch, tmp_path):
    _write_csv(monkeypatch, tmp_path, "date,asset,delta,note\n2024-01-01,BTC,1.0,buy\n2024-02-01,BTC,-3.0,oversell\n")
    with pytest.raises(ValueError) as e:
        twr.load_portfolio()
    assert "oversold" in str(e.value) and "line 3" in str(e.value)


def test_load_portfolio_allows_intraday_net_nonnegative(monkeypatch, tmp_path):
    # A sell logged before its same-day buy nets >= 0 by end of day -> not an oversell.
    _write_csv(monkeypatch, tmp_path, "date,asset,delta,note\n2024-01-01,BTC,-1.0,sell\n2024-01-01,BTC,2.0,buy\n")
    assert len(twr.load_portfolio()) == 2


# --- Price cache I/O (atomic write, corrupt read recovers) ---

def test_cache_roundtrip_and_corrupt_recovers(monkeypatch, tmp_path):
    cache_file = tmp_path / ".price_cache.json"
    monkeypatch.setattr(twr, "CACHE_FILE", cache_file)
    monkeypatch.setattr(twr, "ROOT", tmp_path)  # temp file lands beside it for os.replace
    twr.save_cache({"BTCUSDT:2024-01-01": 42.0})
    assert twr.load_cache() == {"BTCUSDT:2024-01-01": 42.0}
    cache_file.write_text('{"BTCUSDT:2024-01-01": 42.0')  # truncated mid-write
    assert twr.load_cache() == {}  # recovers instead of bricking future refreshes


# --- compute() end to end (injected provider + clock, no network/disk) ---

def test_compute_end_to_end_is_deterministic(monkeypatch, tmp_path):
    _write_csv(monkeypatch, tmp_path, "date,asset,delta,note\n2024-01-01,BTC,1.0,seed\n")
    prices = FakePrices({("BTCUSDT", "2024-01-01"): 100.0, ("BTCUSDT", "2024-01-31"): 200.0})
    data = twr.compute(prices=prices, today=date(2024, 1, 31))
    assert data["as_of"] == "2024-01-31"
    assert data["total_value"] == pytest.approx(200.0)
    assert data["holdings"] == [{"asset": "BTC", "qty": 1.0, "price": 200.0, "value": 200.0}]
    alltime = next(r for r in data["ranges"] if r["name"] == "All-time")
    assert alltime["twr"] == pytest.approx(1.0, abs=1e-3)  # 30-day double
    assert alltime["mwr"] == pytest.approx(1.0, abs=1e-3)
    assert alltime["cagr"] is None  # under a year -> not annualized


def test_compute_drops_fully_exited_dust_position(monkeypatch, tmp_path):
    # 0.1 + 0.1 + 0.1 - 0.3 leaves float dust (~5.5e-17), not an exact 0.
    _write_csv(monkeypatch, tmp_path,
               "date,asset,delta,note\n2024-01-01,BTC,0.1,\n2024-01-02,BTC,0.1,\n"
               "2024-01-03,BTC,0.1,\n2024-01-04,BTC,-0.3,\n")
    flat = FakePrices({("BTCUSDT", f"2024-01-0{d}"): 100.0 for d in range(1, 6)})
    data = twr.compute(prices=flat, today=date(2024, 1, 5))
    assert data["holdings"] == [] and data["total_value"] == pytest.approx(0.0, abs=1e-9)


# --- Binance pricing paths (requests mocked) ---

def test_historical_price_rejects_candle_from_a_later_day(monkeypatch):
    on = "2017-01-01"  # before BTCUSDT listed -> /klines returns the listing-day candle
    later = _day_ms(on) + 5 * 86_400_000
    monkeypatch.setattr(twr.requests, "get", lambda *a, **k: _Resp([[later, "1", "1", "1", "9.0", "1"]]))
    with pytest.raises(RuntimeError):
        twr.historical_price("BTCUSDT", on, {})


def test_historical_price_accepts_same_day_candle(monkeypatch):
    on = "2024-01-01"
    monkeypatch.setattr(twr.requests, "get", lambda *a, **k: _Resp([[_day_ms(on), "1", "1", "1", "42.0", "1"]]))
    assert twr.historical_price("BTCUSDT", on, {}) == 42.0


def test_current_prices_batches_into_one_request(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params)
        return _Resp([{"symbol": "BTCUSDT", "price": "100.0"}, {"symbol": "ETHUSDT", "price": "50.0"}])

    monkeypatch.setattr(twr.requests, "get", fake_get)
    out = twr.current_prices(["BTCUSDT", "ETHUSDT"])
    assert out == {"BTCUSDT": 100.0, "ETHUSDT": 50.0}
    assert len(calls) == 1 and "symbols" in calls[0]


def test_cli_main_handles_binance_errors(monkeypatch):
    def boom():
        raise RuntimeError("binance down")
    monkeypatch.setattr(twr, "compute", boom)
    with pytest.raises(SystemExit):
        twr.main()


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
