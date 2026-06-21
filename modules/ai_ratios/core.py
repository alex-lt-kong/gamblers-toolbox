"""S&P 500 weight computation for the AI-exposure ratio (was ai_ratios.py)."""

import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as _FuturesTimeout

import pandas as pd
import requests
import yfinance as yf

_HTTP_TIMEOUT = 15        # seconds per HTTP request
_YAHOO_DEADLINE = 180     # seconds, overall budget for the market-cap pull


def sp500_tickers() -> list[str]:
    resp = requests.get(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=_HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    # pd.read_html() looks for <table></table> and ignores the rest
    table = pd.read_html(io.StringIO(resp.text))[0]
    return table["Symbol"].str.replace(".", "-", regex=False).tolist()


def sp500_weights() -> tuple[dict[str, float], int, int]:
    """Return (weights%, n_total, n_ok). Weights normalize over the constituents
    that responded, so callers must check coverage = n_ok / n_total before trust."""
    def fetch(ticker):
        try:
            info = yf.Ticker(ticker).info
            price = info.get("regularMarketPrice") or info.get("currentPrice")
            shares = info.get("sharesOutstanding")
            if price and shares:
                return ticker, price * shares
            return ticker, info.get("marketCap")
        except Exception:
            return ticker, None

    tickers = sp500_tickers()
    market_caps: dict[str, float] = {}
    ex = ThreadPoolExecutor(max_workers=8)
    futures = {ex.submit(fetch, t): t for t in tickers}
    try:
        for fut in as_completed(futures, timeout=_YAHOO_DEADLINE):
            t, mc = fut.result()
            if mc:
                market_caps[t] = mc
    except _FuturesTimeout:
        pass  # proceed with whatever arrived; coverage reflects the shortfall
    finally:
        ex.shutdown(wait=False, cancel_futures=True)

    total = sum(market_caps.values())
    weights = {t: mc / total * 100 for t, mc in market_caps.items()} if total else {}
    return weights, len(tickers), len(market_caps)


def index_share(
    tickers: dict[str, float], weights: dict[str, float]
) -> tuple[float, float, list[str]]:
    raw = sum(weights.get(t, 0) for t in tickers)
    adjusted = sum(weights.get(t, 0) * fineness for t, fineness in tickers.items())
    missing = [t for t in tickers if t not in weights]
    return raw, adjusted, missing
