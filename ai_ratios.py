import argparse
import html as htmllib
import io
import sys
from datetime import datetime, timezone
import pandas as pd
import requests
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed


def sp500_tickers() -> list[str]:
    html = requests.get(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        headers={"User-Agent": "Mozilla/5.0"},
    ).text
    # pd.read_html() looks for <table></table> and ignore the rest
    table = pd.read_html(io.StringIO(html))[0]
    return table["Symbol"].str.replace(".", "-", regex=False).tolist()


def sp500_weights(quiet: bool = False) -> dict[str, float]:
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
    if not quiet:
        print(f"Fetching market caps for {len(tickers)} S&P 500 constituents...")
    market_caps = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch, t): t for t in tickers}
        for future in as_completed(futures):
            t, mc = future.result()
            if mc:
                market_caps[t] = mc

    total = sum(market_caps.values())
    return {t: mc / total * 100 for t, mc in market_caps.items()}


def index_share(tickers: dict[str, float], weights: dict[str, float]) -> tuple[float, float, list[str]]:
    raw = sum(weights.get(t, 0) for t in tickers)
    adjusted = sum(weights.get(t, 0) * fineness for t, fineness in tickers.items())
    missing = [t for t in tickers if t not in weights]
    return raw, adjusted, missing


def render_html(tickers: dict[str, float], weights: dict[str, float]) -> str:
    ts_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw, adjusted, missing = index_share(tickers, weights)

    rows = ""
    for t, fineness in sorted(tickers.items(), key=lambda x: weights.get(x[0], 0) * x[1], reverse=True):
        sp_weight = weights.get(t, 0)
        contrib = sp_weight * fineness
        rows += (
            f"<tr>"
            f"<td><code>{htmllib.escape(t)}</code></td>"
            f"<td>{fineness:.0%}</td>"
            f"<td>{sp_weight:.2f}%</td>"
            f"<td>{contrib:.2f}%</td>"
            f"</tr>\n"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Exposure Monitor</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:680px;margin:2rem auto;padding:0 1rem;background:#f8f9fa}}
h1{{font-size:1.3rem}}p.ts{{color:#6c757d;font-size:.85rem;margin-top:-.5rem}}
table{{width:100%;border-collapse:collapse;background:#fff;border-radius:6px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.1)}}
th{{background:#343a40;color:#fff;padding:.55rem .75rem;text-align:left;font-size:.82rem}}
td{{padding:.5rem .75rem;border-bottom:1px solid #dee2e6;font-size:.88rem}}
tr:last-child td{{border-bottom:none}}tr:hover td{{background:#f1f3f5}}
td:nth-child(2),td:nth-child(3),td:nth-child(4){{text-align:right;font-variant-numeric:tabular-nums}}
tfoot td{{font-weight:600;background:#f1f3f5;border-top:2px solid #dee2e6}}
</style>
</head>
<body>
<h1>AI Exposure Monitor</h1>
<p class="ts">Updated: <span id="ts" data-ts="{ts_iso}">{ts_iso}</span></p>
<table>
<thead><tr><th>Ticker</th><th>Fineness</th><th>S&amp;P 500 Weight</th><th>Adjusted Weight</th></tr></thead>
<tbody>
{rows}</tbody>
<tfoot>
<tr><td>Total ({len(tickers)} stocks)</td><td></td><td>{raw:.2f}%</td><td>{adjusted:.2f}%</td></tr>
</tfoot>
</table>
<p style="font-size:.8rem;color:#6c757d">S&amp;P 500 weights sourced from market caps via Yahoo Finance.
<a href="https://www.slickcharts.com/sp500" target="_blank">Sanity check on Slickcharts</a>.</p>
<script>
var el = document.getElementById('ts');
var d = new Date(el.dataset.ts);
var off = -d.getTimezoneOffset();
var sign = off >= 0 ? '+' : '-';
var oh = String(Math.floor(Math.abs(off)/60)).padStart(2,'0');
var om = String(Math.abs(off)%60).padStart(2,'0');
el.textContent = d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0')+'T'+String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0')+':'+String(d.getSeconds()).padStart(2,'0')+sign+oh+':'+om;
</script>
</body>
</html>"""


ai_tickers = {
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

parser = argparse.ArgumentParser()
parser.add_argument("--html", action="store_true")
args = parser.parse_args()

weights = sp500_weights(quiet=args.html)

if args.html:
    print(render_html(ai_tickers, weights))
else:
    raw, adjusted, missing = index_share(ai_tickers, weights)
    print(f"\nAI tickers: {len(ai_tickers)} stocks")
    print(f"Raw S&P 500 share:       {raw:.2f}%")
    print(f"Fineness-adjusted share: {adjusted:.2f}%")
    if missing:
        print(f"Not in S&P 500 (excluded): {', '.join(missing)}")
