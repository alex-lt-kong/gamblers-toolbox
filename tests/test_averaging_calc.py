import pytest
from fastapi.testclient import TestClient

from modules.averaging_calc import calc


def test_gain_dilution():
    d = calc.evaluate(qty=100, avg_cost=100, mkt_px=120, target_pct=15)
    assert d["current_pnl_pct"] == pytest.approx(20)
    assert d["reachable"] is True
    p = d["plan"]
    assert p["shares_to_buy"] == pytest.approx(500 / 18)  # 27.78
    assert p["new_avg_cost"] == pytest.approx(120 / 1.15)
    assert p["new_pnl_pct"] == pytest.approx(15)


def test_loss_average_down():
    p = calc.evaluate(qty=100, avg_cost=100, mkt_px=80, target_pct=-10)["plan"]
    assert p["shares_to_buy"] == pytest.approx(125)
    assert p["new_pnl_pct"] == pytest.approx(-10)


def test_dollar_pnl_unchanged_by_buying_at_market():
    d = calc.evaluate(qty=100, avg_cost=100, mkt_px=120, target_pct=15)
    assert d["pnl_amount"] == pytest.approx(2000)  # qty * (px - avg), pre-buy
    assert d["plan"]["capital_required"] == pytest.approx(d["plan"]["shares_to_buy"] * 120)


def test_whole_share_overshoots_toward_zero():
    p = calc.evaluate(qty=100, avg_cost=100, mkt_px=120, target_pct=15)["plan"]
    assert p["shares_to_buy_whole"] == 28  # ceil(27.78)
    assert p["whole_new_pnl_pct"] < 15  # rounding up dilutes a touch more


@pytest.mark.parametrize("target", [0, 20, 25, -5])  # zero, ==current, past, wrong side
def test_unreachable_target_returns_readout_no_plan(target):
    d = calc.evaluate(qty=100, avg_cost=100, mkt_px=120, target_pct=target)
    assert d["reachable"] is False and d["plan"] is None
    assert d["current_pnl_pct"] == pytest.approx(20)  # readout still served


def test_target_omitted_gives_current_only():
    d = calc.evaluate(qty=100, avg_cost=100, mkt_px=120)
    assert d["plan"] is None and d["current_pnl_pct"] == pytest.approx(20)


@pytest.mark.parametrize("kw", [{"qty": 0}, {"avg_cost": 0}, {"mkt_px": -1}])
def test_non_positive_inputs_rejected(kw):
    with pytest.raises(ValueError):
        calc.evaluate(**{"qty": 100, "avg_cost": 100, "mkt_px": 120, "target_pct": 15, **kw})


INF, NAN = float("inf"), float("nan")


@pytest.mark.parametrize("kw", [
    {"qty": INF}, {"qty": NAN}, {"avg_cost": INF}, {"mkt_px": NAN}, {"target_pct": INF},
])
def test_non_finite_inputs_rejected(kw):
    with pytest.raises(ValueError):
        calc.evaluate(**{"qty": 100, "avg_cost": 100, "mkt_px": 120, "target_pct": 15, **kw})


def test_knife_edge_target_equals_current_never_negative():
    import random
    rng = random.Random(99)
    for _ in range(20000):
        q, c0, p0 = rng.uniform(1, 1e6), rng.uniform(0.5, 5000), rng.uniform(0.5, 5000)
        cur_pct = (p0 / c0 - 1) * 100
        if abs(cur_pct) < 1e-9:
            continue
        d = calc.evaluate(q, c0, p0, cur_pct)  # target == current (float-fragile)
        assert d["plan"] is None or d["plan"]["shares_to_buy"] > 0


def test_finite_inputs_overflowing_result_rejected():
    # Valid, finite, reachable target, but magnitudes overflow the share count.
    with pytest.raises(ValueError):
        calc.evaluate(qty=1e300, avg_cost=1e-200, mkt_px=1e200, target_pct=1e-200)


@pytest.mark.parametrize("args", [
    dict(qty=100, avg_cost=1e-308, mkt_px=1e10),                # current% overflows, current-only
    dict(qty=100, avg_cost=1e-308, mkt_px=1e10, target_pct=10),  # current% overflows, plan still finite
    dict(qty=1e308, avg_cost=1.0, mkt_px=10.0),                 # pnl_amount overflows, current% finite
])
def test_overflowing_readout_rejected(args):
    # Derived readout (current_pnl_pct / pnl_amount) must not leak inf into the JSON.
    with pytest.raises(ValueError):
        calc.evaluate(**args)


# --- API surface ---

def test_api_calc_ok(make_app):
    c = TestClient(make_app())
    r = c.get("/averaging-calc/api/calc", params={"qty": 100, "avg_cost": 100, "mkt_px": 120, "target_pct": 15})
    assert r.status_code == 200 and r.json()["plan"]["new_pnl_pct"] == pytest.approx(15)


def test_api_unreachable_is_200_not_error(make_app):
    c = TestClient(make_app())
    r = c.get("/averaging-calc/api/calc", params={"qty": 100, "avg_cost": 100, "mkt_px": 120, "target_pct": 25})
    assert r.status_code == 200 and r.json()["reachable"] is False and r.json()["plan"] is None


def test_api_current_only_without_target(make_app):
    c = TestClient(make_app())
    r = c.get("/averaging-calc/api/calc", params={"qty": 100, "avg_cost": 100, "mkt_px": 120})
    assert r.status_code == 200 and r.json()["current_pnl_pct"] == pytest.approx(20)


@pytest.mark.parametrize("params", [
    {"qty": "inf", "avg_cost": 100, "mkt_px": 120, "target_pct": 15},
    {"qty": "nan", "avg_cost": 100, "mkt_px": 120, "target_pct": 15},
    {"qty": "1e309", "avg_cost": 100, "mkt_px": 120, "target_pct": 15},  # parses to inf
    {"qty": 100, "avg_cost": 100, "mkt_px": "inf", "target_pct": 15},
    {"qty": 100, "avg_cost": 100, "mkt_px": 120, "target_pct": "nan"},
    {"qty": 0, "avg_cost": 100, "mkt_px": 120, "target_pct": 15},
    {"qty": 100, "avg_cost": "1e-308", "mkt_px": "1e10"},  # readout overflows to inf, no target
])
def test_api_rejects_bad_inputs_with_400_not_500(make_app, params):
    r = TestClient(make_app()).get("/averaging-calc/api/calc", params=params)
    assert r.status_code == 400


def test_page_loads(make_app):
    assert TestClient(make_app()).get("/averaging-calc/").status_code == 200
