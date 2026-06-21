"""Data fetchers for P/E snapshots and historical backfill.

Two backends:
- `YfinanceFetcher`: works for everything Yahoo covers well (US, HK, TW, ADRs).
- `KrxFetcher`: KRX-listed names (.KS/.KQ). Yahoo returns null trailing
  EPS/PE for these — but the underlying quarterly EPS series IS available via
  `get_earnings_dates`, and currency == financialCurrency == KRW, so we can
  reconstruct trailing fields by summing the last 4 reported quarters.
  forward_pe and analyst_count come straight from Yahoo (it has those for KR).

`get_fetcher(ticker)` dispatches by suffix.
"""

from datetime import date, datetime, timedelta
from typing import Protocol

import pandas as pd
import yfinance as yf


class DataFetcher(Protocol):
    def fetch_pe(self, ticker: str) -> dict: ...
    def backfill_history(self, ticker: str, days: int) -> tuple[list[dict], str]: ...


class YfinanceFetcher:
    """Yahoo Finance fetcher. Live snapshot from `.info`; historical TTM P/E
    rebuilt from quarterly EPS calibrated to the live trailingEps value."""

    REPORTING_LAG_DAYS = 45  # Fallback when get_earnings_dates is unavailable

    @staticmethod
    def _first(info: dict, *keys: str):
        """Return the first non-None value. Yahoo mirrors the same number
        under multiple keys (trailingEps / epsTrailingTwelveMonths)."""
        for k in keys:
            v = info.get(k)
            if v is not None:
                return v
        return None

    def fetch_pe(self, ticker: str) -> dict:
        yt = yf.Ticker(ticker)
        info = yt.info

        price = self._first(info, "currentPrice", "regularMarketPrice")
        # yfinance returns 0 for regularMarketVolume on KR tickers (known
        # quirk; the average* fields are populated). Treat 0 as missing so
        # we don't overwrite a real historical value with zero.
        raw_volume = self._first(info, "regularMarketVolume", "volume")
        volume = int(raw_volume) if raw_volume else None
        trailing_eps = self._first(info, "trailingEps", "epsTrailingTwelveMonths")
        forward_eps = self._first(info, "forwardEps", "epsForward")
        ttm_pe = info.get("trailingPE")
        fwd_pe = info.get("forwardPE")
        analyst_count = info.get("numberOfAnalystOpinions")
        trade_ccy = info.get("currency")
        financial_currency = info.get("financialCurrency")

        # Native-currency forward EPS only matters when the company reports in
        # a different currency than it trades in (HK-listed CN cos, ADRs).
        forward_eps_native = None
        if financial_currency and trade_ccy and financial_currency != trade_ccy:
            try:
                df = yt.earnings_estimate
                if df is not None and not df.empty and "+1y" in df.index:
                    v = df.loc["+1y", "avg"]
                    if v is not None and v == v:
                        forward_eps_native = float(v)
            except Exception:
                pass

        if ttm_pe is None and price and trailing_eps and trailing_eps > 0:
            ttm_pe = price / trailing_eps
        if fwd_pe is None and price and forward_eps and forward_eps > 0:
            fwd_pe = price / forward_eps

        return {
            "date": date.today().isoformat(),
            "name": info.get("longName", ""),
            "currency": trade_ccy,
            "price": price,
            "volume": volume,
            "trailing_eps": trailing_eps,
            "forward_eps": forward_eps,
            "ttm_pe": ttm_pe,
            "forward_pe": fwd_pe,
            "analyst_count": analyst_count,
            "financial_currency": financial_currency,
            "forward_eps_native": forward_eps_native,
            "last_crawl_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }

    def backfill_history(self, ticker: str, days: int) -> tuple[list[dict], str]:
        yt = yf.Ticker(ticker)
        info = yt.info
        name = info.get("longName", "")
        currency = info.get("currency")
        financial_currency = info.get("financialCurrency")
        true_ttm_eps = info.get("trailingEps")

        prices = yt.history(period=f"{days}d", auto_adjust=True)
        if prices.empty:
            return [], "no price history from yfinance"

        eps_history = self._eps_history(yt, true_ttm_eps)

        # Price is recorded for every trading day regardless of EPS coverage —
        # TTM P/E simply stays NULL on days that lack 4 reported quarters (e.g.
        # a recent spinoff or fresh listing). Those price rows still matter:
        # the IBES forward-P/E line interpolates against the daily price, so
        # gating them on TTM availability would blank out the whole pre-4Q
        # window even though the prices exist at the source.
        rows = []
        ttm_count = 0
        for ts, prow in prices.iterrows():
            d = ts.date() if hasattr(ts, "date") else ts
            price = float(prow["Close"])
            raw_vol = prow.get("Volume")
            volume = int(raw_vol) if raw_vol is not None and pd.notna(raw_vol) else None
            applicable = [eps for rd, eps in eps_history if rd <= d]
            if len(applicable) >= 4:
                ttm_eps = sum(applicable[-4:])
                ttm_pe = price / ttm_eps if ttm_eps > 0 else None
                ttm_count += 1
            else:
                ttm_eps = ttm_pe = None
            rows.append({
                "date": d.isoformat(),
                "name": name,
                "currency": currency,
                "price": price,
                "volume": volume,
                "trailing_eps": ttm_eps,
                "forward_eps": None,
                "ttm_pe": ttm_pe,
                "forward_pe": None,
                "analyst_count": None,
                "financial_currency": financial_currency,
                "forward_eps_native": None,
            })

        if not rows:
            return [], "no price history from yfinance"
        suffix = "" if ttm_count == len(rows) else f" ({len(rows) - ttm_count} price-only, <4Q EPS)"
        return rows, "ok" + suffix

    def _eps_history(self, ticker_obj, true_ttm_eps):
        """Return [(available_date, scaled_eps), ...] oldest-first. Calibrates
        raw values by `true_ttm_eps / sum(latest 4 reported)` so units match
        the trading currency. When `true_ttm_eps` is falsy (e.g. KR names
        where Yahoo's aggregate is null but quarterly is already in KRW),
        scalar falls through to 1.0 and the raw values are used as-is."""
        try:
            ed = ticker_obj.get_earnings_dates(limit=25)
            if ed is not None and not ed.empty:
                ed = ed.dropna(subset=["Reported EPS"]).sort_index()
                if len(ed) >= 4:
                    raw_recent_4 = float(ed["Reported EPS"].iloc[-4:].sum())
                    scalar = (
                        (true_ttm_eps / raw_recent_4)
                        if (raw_recent_4 and true_ttm_eps)
                        else 1.0
                    )
                    return [
                        ((ts.date() if hasattr(ts, "date") else ts),
                         float(row["Reported EPS"]) * scalar)
                        for ts, row in ed.iterrows()
                    ]
        except Exception:
            pass

        qfin = ticker_obj.quarterly_income_stmt
        if qfin is None or qfin.empty:
            return []
        eps_row = None
        for label in ("Diluted EPS", "Basic EPS"):
            if label in qfin.index:
                eps_row = qfin.loc[label]
                break
        if eps_row is None:
            return []
        raw = []
        for col, val in eps_row.items():
            if val is None or pd.isna(val):
                continue
            qe_date = col.date() if hasattr(col, "date") else col
            raw.append((qe_date, float(val)))
        if len(raw) < 4:
            return []
        raw.sort(key=lambda x: x[0])
        raw_recent_4 = sum(eps for _, eps in raw[-4:])
        scalar = (
            (true_ttm_eps / raw_recent_4) if (raw_recent_4 and true_ttm_eps) else 1.0
        )
        lag = timedelta(days=self.REPORTING_LAG_DAYS)
        return [(qe + lag, eps * scalar) for qe, eps in raw]


class KrxFetcher:
    """KRX-listed names (.KS/.KQ). Composes YfinanceFetcher and patches the
    two fields Yahoo leaves null for KR: trailing EPS (computed by summing
    the last 4 reported quarters from `get_earnings_dates`) and forward EPS
    (derived from price / forwardPE since Yahoo gives the ratio but not the
    underlying EPS for KR).

    Currency is uniformly KRW for KRX-listed names (currency == financial
    currency), so no calibration is needed — the raw quarterly EPS values
    from yfinance are already in the trading currency.

    Backfill is delegated wholesale to YfinanceFetcher: its existing
    quarterly-EPS aggregation in `_eps_history` falls through to scalar=1.0
    when `info.trailingEps` is null, which is exactly what KR needs.
    """

    def __init__(self, base: DataFetcher):
        self._base = base

    def fetch_pe(self, ticker: str) -> dict:
        snap = self._base.fetch_pe(ticker)

        if snap.get("trailing_eps") is None:
            try:
                ed = yf.Ticker(ticker).get_earnings_dates(limit=8)
                if ed is not None and not ed.empty:
                    ed = ed.dropna(subset=["Reported EPS"]).sort_index()
                    if len(ed) >= 4:
                        ttm_eps = float(ed["Reported EPS"].iloc[-4:].sum())
                        snap["trailing_eps"] = ttm_eps
                        if snap.get("price") and ttm_eps > 0:
                            snap["ttm_pe"] = snap["price"] / ttm_eps
            except Exception:
                pass

        if snap.get("forward_eps") is None and snap.get("forward_pe") and snap.get("price"):
            fp = snap["forward_pe"]
            if fp > 0:
                snap["forward_eps"] = snap["price"] / fp

        return snap

    def backfill_history(self, ticker: str, days: int) -> tuple[list[dict], str]:
        return self._base.backfill_history(ticker, days)


_YF = YfinanceFetcher()
_KRX = KrxFetcher(base=_YF)


def get_fetcher(ticker: str) -> DataFetcher:
    """Dispatch by ticker suffix. .KS/.KQ → KRX; everything else → Yahoo."""
    t = ticker.upper()
    if t.endswith(".KS") or t.endswith(".KQ"):
        return _KRX
    return _YF
