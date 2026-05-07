#!/usr/bin/env python3
"""
Fetch TTM and Forward P/E ratios for a list of tickers from a JSON file.
Data source: Yahoo Finance via yfinance (free, no API key required).
"""

import json
import sys
import argparse
import html
from datetime import datetime, timezone
import yfinance as yf


def load_tickers(path: str) -> list[str]:
    with open(path) as f:
        data = json.load(f)
    tickers = data.get("tickers", [])
    if not isinstance(tickers, list) or not tickers:
        raise ValueError(f"'tickers' must be a non-empty list in {path}")
    return [t.upper().strip() for t in tickers]


def fetch_pe(ticker: str) -> dict:
    info = yf.Ticker(ticker).info

    price = info.get("currentPrice") or info.get("regularMarketPrice")
    trailing_eps = info.get("trailingEps")
    forward_eps = info.get("forwardEps")

    # Yahoo Finance sometimes provides PE directly; use it as a fallback
    ttm_pe = info.get("trailingPE")
    fwd_pe = info.get("forwardPE")

    # Recompute from price + EPS when the pre-computed field is absent
    if ttm_pe is None and price and trailing_eps and trailing_eps > 0:
        ttm_pe = price / trailing_eps
    if fwd_pe is None and price and forward_eps and forward_eps > 0:
        fwd_pe = price / forward_eps

    return {
        "ticker": ticker,
        "name": info.get("longName", ""),
        "price": price,
        "trailing_eps": trailing_eps,
        "forward_eps": forward_eps,
        "ttm_pe": ttm_pe,
        "forward_pe": fwd_pe,
    }


def fmt(value, decimals: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{value:.{decimals}f}"


def print_table(results: list[dict]) -> None:
    ticker_w = max(len(r["ticker"]) for r in results) + 2
    ticker_w = max(ticker_w, 8)
    name_w = max((len(r["name"]) for r in results if r["name"]), default=0) + 2
    name_w = max(name_w, 14)  # min width for "Company Name" header
    col_w = {"ticker": ticker_w, "name": name_w, "price": 10, "ttm_pe": 10, "forward_pe": 12}
    header = (
        f"{'Ticker':<{col_w['ticker']}}"
        f"{'Company Name':<{col_w['name']}}"
        f"{'Price ($)':>{col_w['price']}}"
        f"{'TTM P/E':>{col_w['ttm_pe']}}"
        f"{'Forward P/E':>{col_w['forward_pe']}}"
    )
    sep = "-" * len(header)
    print(sep)
    print(header)
    print(sep)
    for r in results:
        print(
            f"{r['ticker']:<{col_w['ticker']}}"
            f"{(r['name'] or ''):<{col_w['name']}}"
            f"{fmt(r['price']):>{col_w['price']}}"
            f"{fmt(r['ttm_pe']):>{col_w['ttm_pe']}}"
            f"{fmt(r['forward_pe']):>{col_w['forward_pe']}}"
        )
    print(sep)


def render_html(results: list[dict]) -> str:
    ts_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = ""
    for r in results:
        rows += (
            f"<tr>"
            f"<td><code>{html.escape(r['ticker'])}</code></td>"
            f"<td>{html.escape(r['name'] or '')}</td>"
            f"<td>{fmt(r['price'])}</td>"
            f"<td>{fmt(r['ttm_pe'])}</td>"
            f"<td>{fmt(r['forward_pe'])}</td>"
            f"</tr>\n"
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>P/E Monitor</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;background:#f8f9fa}}
h1{{font-size:1.3rem}}p.ts{{color:#6c757d;font-size:.85rem;margin-top:-.5rem}}
table{{width:100%;border-collapse:collapse;background:#fff;border-radius:6px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.1)}}
th{{background:#343a40;color:#fff;padding:.55rem .75rem;text-align:left;font-size:.82rem}}
td{{padding:.5rem .75rem;border-bottom:1px solid #dee2e6;font-size:.88rem}}
tr:last-child td{{border-bottom:none}}tr:hover td{{background:#f1f3f5}}
td:nth-child(3),td:nth-child(4),td:nth-child(5){{text-align:right;font-variant-numeric:tabular-nums}}
</style>
</head>
<body>
<h1>P/E Monitor</h1>
<p class="ts">Updated: <span id="ts" data-ts="{ts_iso}">{ts_iso}</span></p>
<table>
<thead><tr><th>Ticker</th><th>Company</th><th>Price ($)</th><th>TTM P/E</th><th>Forward P/E</th></tr></thead>
<tbody>
{rows}</tbody>
</table>
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


def main() -> None:
    parser = argparse.ArgumentParser(description="P/E ratio fetcher (TTM + Forward)")
    parser.add_argument(
        "file",
        nargs="?",
        default="tickers.json",
        help="Path to JSON file with a 'tickers' list (default: tickers.json)",
    )
    parser.add_argument("--html", action="store_true", help="Output an HTML page instead of plain text")
    args = parser.parse_args()

    try:
        tickers = load_tickers(args.file)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
        sys.exit(f"Error loading tickers: {e}")

    if not args.html:
        print(f"\nFetching P/E data for: {', '.join(tickers)}\n")
    results = []
    for t in tickers:
        try:
            results.append(fetch_pe(t))
        except Exception as e:
            print(f"  Warning: could not fetch data for {t}: {e}")
            results.append({"ticker": t, "price": None, "ttm_pe": None, "forward_pe": None})

    results.sort(key=lambda r: (r["forward_pe"] is None, r["forward_pe"] or 0))
    if args.html:
        print(render_html(results))
    else:
        print_table(results)


if __name__ == "__main__":
    main()
