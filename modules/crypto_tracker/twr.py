"""TWR / MWR / CAGR for a manually-maintained multi-asset crypto portfolio.

CSV schema (portfolio.csv):
    date,asset,delta,note
    YYYY-MM-DD,<ASSET>,<float>,<free text>

Each row is one transaction in one asset; `delta` is the change in that asset's
quantity (positive = buy/inflow, negative = sell/withdrawal). The USD value of a
flow is `delta * <ASSET>USDT close` on its date (Binance public klines). Holdings
per asset are the cumulative sum of deltas; the basket is valued as one
USD-denominated portfolio.

`compute()` returns the live portfolio plus per-range returns; the module's cache
serves it and a scheduler refreshes it. Assets live in config.ASSET_SYMBOLS.
"""

import csv
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from . import config

ROOT = Path(__file__).resolve().parent
PORTFOLIO_CSV = ROOT / "portfolio.csv"
CACHE_FILE = ROOT / ".price_cache.json"

# Binance public API — no auth. USDT pairs proxy USD (tracking error <0.3%).
BINANCE_BASE = "https://api.binance.com/api/v3"

# Plain YYYY-MM-DD or any ISO-8601 starting with one; the time part is dropped
# (pricing is at daily UTC-close resolution).
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ].*)?$")


def symbol_for(asset):
    if asset not in config.ASSET_SYMBOLS:
        raise ValueError(f"Unknown asset {asset!r}. Add it to config.ASSET_SYMBOLS.")
    return config.ASSET_SYMBOLS[asset]


def load_portfolio():
    if not PORTFOLIO_CSV.exists():
        return []
    rows = []
    with PORTFOLIO_CSV.open() as f:
        for row in csv.DictReader(f):
            d = (row.get("date") or "").strip()
            if not DATE_RE.match(d):
                continue
            asset = (row.get("asset") or "").strip().upper()
            if not asset:
                continue
            rows.append({
                "date": d[:10],
                "asset": asset,
                "delta": float(row["delta"]),
                "note": (row.get("note") or "").strip(),
            })
    rows.sort(key=lambda r: r["date"])
    return rows


def load_cache():
    if CACHE_FILE.exists():
        with CACHE_FILE.open() as f:
            return json.load(f)
    return {}


def save_cache(cache):
    with CACHE_FILE.open("w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)


def historical_price(symbol, on_date, cache):
    """USD-proxy close for `symbol` on YYYY-MM-DD (UTC day). Cached: immutable."""
    key = f"{symbol}:{on_date}"
    if key in cache:
        return cache[key]
    d = datetime.strptime(on_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    resp = requests.get(
        f"{BINANCE_BASE}/klines",
        params={"symbol": symbol, "interval": "1d",
                "startTime": int(d.timestamp() * 1000), "limit": 1},
        timeout=30,
    )
    resp.raise_for_status()
    klines = resp.json()
    if not klines:
        raise RuntimeError(f"No Binance kline for {symbol} on {on_date}")
    cache[key] = float(klines[0][4])  # close
    return cache[key]


def current_price(symbol):
    resp = requests.get(
        f"{BINANCE_BASE}/ticker/price", params={"symbol": symbol}, timeout=30
    )
    resp.raise_for_status()
    return float(resp.json()["price"])


def balance_at(rows, on_date, asset):
    return sum(r["delta"] for r in rows if r["date"] <= on_date and r["asset"] == asset)


def price_at(symbol, on_date, today_str, today_prices, cache):
    return today_prices[symbol] if on_date == today_str else historical_price(symbol, on_date, cache)


def portfolio_value_at(rows, on_date, assets, today_str, today_prices, cache):
    total = 0.0
    for asset in assets:
        bal = balance_at(rows, on_date, asset)
        if bal == 0:
            continue
        total += bal * price_at(symbol_for(asset), on_date, today_str, today_prices, cache)
    return total


def flow_at(rows, on_date, today_str, today_prices, cache):
    """Sum of (delta * price) for every row on exactly `on_date`."""
    total = 0.0
    for r in rows:
        if r["date"] != on_date:
            continue
        total += r["delta"] * price_at(symbol_for(r["asset"]), on_date, today_str, today_prices, cache)
    return total


def xnpv(rate, cashflows):
    """NPV with irregular dates. cashflows = [(date_obj, amount), ...]."""
    if rate <= -1:
        return float("inf")
    t0 = cashflows[0][0]
    return sum(amt / (1 + rate) ** ((d - t0).days / 365.0) for d, amt in cashflows)


def xirr(cashflows):
    """Annualized IRR via bisection. Returns None if no sign change in bracket."""
    if not any(amt > 0 for _, amt in cashflows) or not any(amt < 0 for _, amt in cashflows):
        return None
    low, high = -0.9999, 1000.0
    f_low, f_high = xnpv(low, cashflows), xnpv(high, cashflows)
    if f_low * f_high > 0:
        return None
    mid = (low + high) / 2
    for _ in range(200):
        mid = (low + high) / 2
        f_mid = xnpv(mid, cashflows)
        if abs(f_mid) < 1e-7 or high - low < 1e-10:
            return mid
        if f_low * f_mid < 0:
            high, f_high = mid, f_mid
        else:
            low, f_low = mid, f_mid
    return mid


def annualize_cumul(cumul, days):
    """Cumulative-period return -> annualized rate (CAGR).

    None for windows under ~1 year — annualizing a short window is a misleading
    projection, not a measured rate.
    """
    if cumul is None or days < 360:
        return None
    if cumul <= -1:
        return -1.0
    return (1 + cumul) ** (365.0 / days) - 1


def mwr_over_range(rows, assets, start_date, end_date, today_str, today_prices, cache):
    """Cumulative money-weighted return over [start_date, end_date].

    V(start) is an initial deposit, each in-period flow a cash flow, V(end) a
    terminal withdrawal; solves annualized IRR, then converts to a cumulative
    period return so it is directly comparable to TWR.
    """
    if start_date >= end_date:
        return None

    v_start = portfolio_value_at(rows, start_date, assets, today_str, today_prices, cache)
    v_end = portfolio_value_at(rows, end_date, assets, today_str, today_prices, cache)
    if v_start <= 0:
        return None

    flows = [(datetime.strptime(start_date, "%Y-%m-%d").date(), -v_start)]
    for r in rows:
        if start_date < r["date"] <= end_date:
            p = price_at(symbol_for(r["asset"]), r["date"], today_str, today_prices, cache)
            d = datetime.strptime(r["date"], "%Y-%m-%d").date()
            flows.append((d, -(r["delta"] * p)))
    flows.append((datetime.strptime(end_date, "%Y-%m-%d").date(), v_end))

    annual = xirr(flows)
    if annual is None:
        return None
    period_years = (flows[-1][0] - flows[0][0]).days / 365.0
    if period_years <= 0:
        return None
    return (1 + annual) ** period_years - 1


def twr_over_range(rows, assets, start_date, end_date, today_str, today_prices, cache):
    """Sub-period returns geometrically linked from start_date to end_date.

    Boundaries: start_date, every interior row date, and end_date. The flow at a
    boundary is the sum of (delta * price) on that date; the flow embedded in
    V_start is not counted as a period return.
    """
    if start_date >= end_date:
        return None

    v_prev = portfolio_value_at(rows, start_date, assets, today_str, today_prices, cache)
    if v_prev <= 0:
        return None

    interior = [r for r in rows if start_date < r["date"] <= end_date]
    boundary_dates = sorted({r["date"] for r in interior})
    if not boundary_dates or boundary_dates[-1] != end_date:
        boundary_dates.append(end_date)

    product = 1.0
    for d in boundary_dates:
        v_cur = portfolio_value_at(rows, d, assets, today_str, today_prices, cache)
        flow = flow_at(rows, d, today_str, today_prices, cache)
        if v_prev == 0:
            v_prev = v_cur
            continue
        product *= 1 + (v_cur - flow - v_prev) / v_prev
        v_prev = v_cur
    return product - 1


def _range_starts(today, first_date):
    return [
        ("All-time", first_date),
        ("5Y", (today - timedelta(days=365 * 5)).isoformat()),
        ("3Y", (today - timedelta(days=365 * 3)).isoformat()),
        ("YTD", date(today.year, 1, 1).isoformat()),
        ("1Y", (today - timedelta(days=365)).isoformat()),
        ("90D", (today - timedelta(days=90)).isoformat()),
        ("30D", (today - timedelta(days=30)).isoformat()),
    ]


def compute():
    """Fetch live prices and return holdings + per-range TWR/MWR/CAGR.

    Loads/saves the on-disk historical-price cache. Raises ValueError when the
    portfolio is empty (nothing to value).
    """
    rows = load_portfolio()
    if not rows:
        raise ValueError("portfolio.csv has no transactions")

    assets = sorted({r["asset"] for r in rows})
    for a in assets:
        symbol_for(a)  # validate before any network call

    cache = load_cache()
    today = date.today()
    today_str = today.isoformat()
    today_prices = {symbol_for(a): current_price(symbol_for(a)) for a in assets}
    first_date = rows[0]["date"]

    holdings, total_value = [], 0.0
    for asset in assets:
        bal = balance_at(rows, today_str, asset)
        if bal == 0:
            continue
        price = today_prices[symbol_for(asset)]
        total_value += bal * price
        holdings.append({"asset": asset, "qty": bal, "price": price, "value": bal * price})

    ranges = []
    for name, start in _range_starts(today, first_date):
        start = max(start, first_date)
        days = (today - datetime.strptime(start, "%Y-%m-%d").date()).days
        twr = twr_over_range(rows, assets, start, today_str, today_str, today_prices, cache)
        mwr = mwr_over_range(rows, assets, start, today_str, today_str, today_prices, cache)
        ranges.append({
            "name": name, "start": start, "days": days,
            "twr": twr, "mwr": mwr, "cagr": annualize_cumul(twr, days),
        })

    save_cache(cache)
    return {
        "computed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "as_of": today_str,
        "total_value": total_value,
        "holdings": holdings,
        "ranges": ranges,
    }


def _fmt_pct(x):
    return f"{x * 100:>9.2f}%" if x is not None else f"{'n/a':>10}"


def main():
    """Standalone CLI: print the portfolio and return table."""
    try:
        data = compute()
    except ValueError as e:
        print(e, file=sys.stderr)
        sys.exit(1)

    print(f"Portfolio  (as of {data['as_of']})")
    for h in data["holdings"]:
        qty = f"{h['qty']:.9f}".rstrip("0").rstrip(".")
        print(f"  {h['asset']:<4}  {qty} @ ${h['price']:,.2f} = ${h['value']:,.2f}")
    print(f"  Total value:    ${data['total_value']:,.2f}")
    print()
    print(f"{'Range':<10} {'TWR':>10} {'MWR':>10} {'CAGR':>10}")
    print(f"{'-' * 10} {'-' * 10} {'-' * 10} {'-' * 10}")
    for r in data["ranges"]:
        print(f"{r['name']:<10} {_fmt_pct(r['twr'])} {_fmt_pct(r['mwr'])} {_fmt_pct(r['cagr'])}")


if __name__ == "__main__":
    main()
