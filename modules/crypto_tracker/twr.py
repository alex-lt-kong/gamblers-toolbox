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
import math
import os
import re
import sys
import tempfile
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


def _parse_date(value):
    """A real calendar date from YYYY-MM-DD (optionally + time), else None.

    DATE_RE only checks shape, so e.g. 2023-13-45 passes it; strptime rejects it.
    """
    if not DATE_RE.match(value):
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def load_portfolio():
    """Parse portfolio.csv. Reject any non-blank malformed row (with its line
    number) rather than skip it silently — a dropped flow misreports holdings,
    and a NaN/Inf delta would propagate until JSON serialization fails."""
    if not PORTFOLIO_CSV.exists():
        return []
    rows, errors = [], []
    with PORTFOLIO_CSV.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            line = reader.line_num
            date_str = (row.get("date") or "").strip()
            asset = (row.get("asset") or "").strip().upper()
            delta_str = (row.get("delta") or "").strip()
            if not (date_str or asset or delta_str):
                continue  # genuinely blank row
            day = _parse_date(date_str)
            if day is None:
                errors.append(f"line {line}: invalid date {date_str!r}")
                continue
            if asset not in config.ASSET_SYMBOLS:
                errors.append(f"line {line}: unknown asset {asset!r}")
                continue
            try:
                delta = float(delta_str)
            except ValueError:
                errors.append(f"line {line}: non-numeric delta {delta_str!r}")
                continue
            if not math.isfinite(delta):
                errors.append(f"line {line}: non-finite delta {delta_str!r}")
                continue
            rows.append({
                "date": day.isoformat(),
                "asset": asset,
                "delta": delta,
                "note": (row.get("note") or "").strip(),
            })
    if errors:
        raise ValueError("portfolio.csv has invalid rows:\n  " + "\n  ".join(errors))
    rows.sort(key=lambda r: r["date"])
    return rows


def load_cache():
    if not CACHE_FILE.exists():
        return {}
    try:
        with CACHE_FILE.open() as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}  # corrupt/truncated cache: rebuild rather than brick refreshes
    return data if isinstance(data, dict) else {}


def save_cache(cache):
    """Write atomically (temp + os.replace) so a crash mid-write can't leave a
    truncated file that bricks every future refresh."""
    fd, tmp = tempfile.mkstemp(dir=str(ROOT), prefix=".price_cache.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(cache, f, indent=2, sort_keys=True)
        os.replace(tmp, CACHE_FILE)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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


class BinancePrices:
    """Price provider: today's value from the live ticker, prior days from the
    daily close (lazily fetched and memoized in `history`). Injectable so
    compute() can be driven deterministically in tests."""

    def __init__(self, today_str, today_prices, history):
        self._today_str = today_str
        self._today_prices = today_prices
        self._history = history

    def price(self, symbol, on_date):
        if on_date == self._today_str:
            return self._today_prices[symbol]
        return historical_price(symbol, on_date, self._history)


def portfolio_value_at(rows, on_date, assets, prices):
    total = 0.0
    for asset in assets:
        bal = balance_at(rows, on_date, asset)
        if bal == 0:
            continue
        total += bal * prices.price(symbol_for(asset), on_date)
    return total


def flow_at(rows, on_date, prices):
    """Sum of (delta * price) for every row on exactly `on_date`."""
    total = 0.0
    for r in rows:
        if r["date"] != on_date:
            continue
        total += r["delta"] * prices.price(symbol_for(r["asset"]), on_date)
    return total


def xnpv(rate, cashflows):
    """NPV with irregular dates. cashflows = [(date_obj, amount), ...]."""
    if rate <= -1:
        return float("inf")
    t0 = cashflows[0][0]
    return sum(amt / (1 + rate) ** ((d - t0).days / 365.0) for d, amt in cashflows)


def xirr(cashflows):
    """Annualized IRR via bisection in log-rate space (x = ln(1 + r)).

    A fixed rate bracket caps the gain it can represent (e.g. high=1000 tops out
    at a 76% 30-day gain), so a short window with a large gain — which implies an
    enormous annual rate — would find no sign change and return None. Bracketing
    on x = ln(1 + r) and expanding the upper bound resolves those. The cap keeps
    both exp(x) and (1 + r)**t below float overflow. Returns None if unbracketed.
    """
    if not any(amt > 0 for _, amt in cashflows) or not any(amt < 0 for _, amt in cashflows):
        return None

    def f(x):
        return xnpv(math.expm1(x), cashflows)  # expm1(x) = e**x - 1 = r

    t_max = max((cashflows[-1][0] - cashflows[0][0]).days, 1) / 365.0
    x_cap = min(80.0, 690.0 / t_max)

    lo, hi = math.log(1e-4), 1.0  # r in [-0.9999, e-1]
    f_lo, f_hi = f(lo), f(hi)
    while f_lo * f_hi > 0:
        hi += 4.0
        if hi >= x_cap:
            return None
        f_hi = f(hi)

    mid = (lo + hi) / 2
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = f(mid)
        if abs(f_mid) < 1e-7 or hi - lo < 1e-12:
            break
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return math.expm1(mid)


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


def mwr_over_range(rows, assets, start_date, end_date, prices):
    """Cumulative money-weighted return over [start_date, end_date].

    V(start) is an initial deposit, each in-period flow a cash flow, V(end) a
    terminal withdrawal; solves annualized IRR, then converts to a cumulative
    period return so it is directly comparable to TWR.
    """
    if start_date >= end_date:
        return None

    v_start = portfolio_value_at(rows, start_date, assets, prices)
    v_end = portfolio_value_at(rows, end_date, assets, prices)
    if v_start <= 0:
        return None

    flows = [(datetime.strptime(start_date, "%Y-%m-%d").date(), -v_start)]
    for r in rows:
        if start_date < r["date"] <= end_date:
            p = prices.price(symbol_for(r["asset"]), r["date"])
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


def twr_over_range(rows, assets, start_date, end_date, prices):
    """Sub-period returns geometrically linked from start_date to end_date.

    Boundaries: start_date, every interior row date, and end_date. The flow at a
    boundary is the sum of (delta * price) on that date; the flow embedded in
    V_start is not counted as a period return.
    """
    if start_date >= end_date:
        return None

    v_prev = portfolio_value_at(rows, start_date, assets, prices)
    if v_prev <= 0:
        return None

    interior = [r for r in rows if start_date < r["date"] <= end_date]
    boundary_dates = sorted({r["date"] for r in interior})
    if not boundary_dates or boundary_dates[-1] != end_date:
        boundary_dates.append(end_date)

    product = 1.0
    for d in boundary_dates:
        v_cur = portfolio_value_at(rows, d, assets, prices)
        flow = flow_at(rows, d, prices)
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


def compute(prices=None, today=None):
    """Return holdings + per-range TWR/MWR/CAGR.

    `prices` (a provider with `.price(symbol, date)`) and `today` are injectable
    for deterministic tests; left None they hit live Binance and load/save the
    on-disk price cache. Raises ValueError when the portfolio is empty.
    """
    rows = load_portfolio()
    if not rows:
        raise ValueError("portfolio.csv has no transactions")

    assets = sorted({r["asset"] for r in rows})
    for a in assets:
        symbol_for(a)  # validate before any network call

    today = today or date.today()
    today_str = today.isoformat()

    persist = prices is None
    if persist:
        cache = load_cache()
        today_prices = {symbol_for(a): current_price(symbol_for(a)) for a in assets}
        prices = BinancePrices(today_str, today_prices, cache)

    first_date = rows[0]["date"]
    holdings, total_value = [], 0.0
    for asset in assets:
        bal = balance_at(rows, today_str, asset)
        if bal == 0:
            continue
        price = prices.price(symbol_for(asset), today_str)
        total_value += bal * price
        holdings.append({"asset": asset, "qty": bal, "price": price, "value": bal * price})

    ranges = []
    for name, start in _range_starts(today, first_date):
        start = max(start, first_date)
        days = (today - datetime.strptime(start, "%Y-%m-%d").date()).days
        twr = twr_over_range(rows, assets, start, today_str, prices)
        mwr = mwr_over_range(rows, assets, start, today_str, prices)
        ranges.append({
            "name": name, "start": start, "days": days,
            "twr": twr, "mwr": mwr, "cagr": annualize_cumul(twr, days),
        })

    if persist:
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
