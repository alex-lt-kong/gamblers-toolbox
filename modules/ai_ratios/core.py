"""S&P 500 weight computation for the AI-exposure ratio (was ai_ratios.py)."""

import io
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
import yfinance as yf


def sp500_tickers() -> list[str]:
    html = requests.get(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        headers={"User-Agent": "Mozilla/5.0"},
    ).text
    # pd.read_html() looks for <table></table> and ignores the rest
    table = pd.read_html(io.StringIO(html))[0]
    return table["Symbol"].str.replace(".", "-", regex=False).tolist()


def sp500_weights() -> dict[str, float]:
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
    market_caps = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch, t): t for t in tickers}
        for future in as_completed(futures):
            t, mc = future.result()
            if mc:
                market_caps[t] = mc

    total = sum(market_caps.values())
    return {t: mc / total * 100 for t, mc in market_caps.items()}


def index_share(
    tickers: dict[str, float], weights: dict[str, float]
) -> tuple[float, float, list[str]]:
    raw = sum(weights.get(t, 0) for t in tickers)
    adjusted = sum(weights.get(t, 0) * fineness for t, fineness in tickers.items())
    missing = [t for t in tickers if t not in weights]
    return raw, adjusted, missing
