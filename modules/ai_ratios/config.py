"""AI Ratios config: the AI-exposure basket and refresh cadence.

Each ticker maps to a "fineness" — the fraction of the company's value
attributable to AI exposure.
"""

AI_TICKERS = {
    "NVDA": 1.00,
    "AVGO": 0.55,
    "AMD":  0.60,
    "MU":   0.65,
    "QCOM": 0.25,
    "MSFT": 0.50,
    "AMZN": 0.40,
    "GOOGL": 0.60,
    "GOOG": 0.60,
    "META": 0.50,
    "ORCL": 0.35,
    "LRCX": 0.45,
    "SNDK": 0.25,
    "INTC": 0.30,
}

REFRESH_INTERVAL_SECONDS = 6 * 60 * 60
