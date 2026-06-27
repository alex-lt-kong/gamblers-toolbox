"""Crypto Tracker config: the tracked assets and refresh cadence.

Each asset maps to its Binance USDT pair (used as a USD proxy). To track a new
asset, add its pair here and record its flows in portfolio.csv.
"""

ASSET_SYMBOLS = {
    "ETH": "ETHUSDT",
    "BTC": "BTCUSDT",
}

REFRESH_INTERVAL_SECONDS = 15 * 60
