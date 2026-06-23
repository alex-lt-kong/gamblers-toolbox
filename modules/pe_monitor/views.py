"""FastAPI router for the P/E Monitor module: dashboard + JSON API.

Was app.py. Config is loaded lazily and the scheduler is owned by `lifespan`, so
importing this module has no side effects.
"""

from contextlib import contextmanager
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from . import config, scheduler, storage

SLUG = "pe-monitor"
_HERE = Path(__file__).resolve().parent

router = APIRouter()
templates = Jinja2Templates(directory=str(_HERE / "templates"))


@lru_cache(maxsize=1)
def _cfg() -> dict:
    return config.load_config()


# Predefined ranges for /api/history. None = no lower bound (all).
RANGE_DAYS = {
    "1m": 31, "3m": 92, "6m": 183,
    "1y": 366, "3y": 3 * 366, "5y": 5 * 366, "10y": 10 * 366,
    "all": None,
}

# Lookback windows for the Δ-forward-P/E page. Day counts mirror RANGE_DAYS;
# "ytd" is special-cased (baseline = latest value on/before Jan 1).
DELTA_WINDOWS = {"1d": 1, "1w": 7, "1m": 31, "3m": 92, "6m": 183, "1y": 366}

# Adaptive downsampling: pick the smallest bucket that keeps the response
# under TARGET_POINTS. Within each bucket we keep the last (most recent)
# observation — P/E moves slowly enough that last-in-bucket reads cleanly.
TARGET_POINTS = 800
BUCKET_DAYS = (1, 7, 30, 90)


def _pick_bucket(num_rows: int) -> int:
    for b in BUCKET_DAYS:
        if num_rows / b <= TARGET_POINTS:
            return b
    return BUCKET_DAYS[-1]


def _downsample(rows: list[dict], bucket_days: int) -> list[dict]:
    if bucket_days <= 1 or len(rows) <= TARGET_POINTS:
        return rows
    out = []
    current_bucket = None
    bucket_rows: list[dict] = []
    for r in rows:
        b = date.fromisoformat(r["date"]).toordinal() // bucket_days
        if current_bucket is None:
            current_bucket = b
        if b != current_bucket:
            out.append(_collapse_bucket(bucket_rows))
            current_bucket = b
            bucket_rows = []
        bucket_rows.append(r)
    if bucket_rows:
        out.append(_collapse_bucket(bucket_rows))
    return out


def _collapse_bucket(bucket_rows: list[dict]) -> dict:
    """Collapse a bucket of daily rows into one. Most fields take the last
    (most recent) value; volume sums so the bar represents period volume."""
    result = dict(bucket_rows[-1])
    vols = [r.get("volume") for r in bucket_rows]
    vols = [v for v in vols if v is not None]
    result["volume"] = sum(vols) if vols else None
    return result


def _interpolate_series(rows: list[dict], value_col: str, flag_col: str) -> list[dict]:
    """Reconstruct a daily forward-P/E line from sparse, signed anchors, breaking
    it wherever the implied forward EPS is non-positive (a forecast loss leaves
    the P/E undefined).

    Why EPS space: at any anchor date d we know Price(d) and the (signed) P/E(d)
    from real data, so ForwardEPS(d) = Price(d) / PE(d) is determined. Between
    earnings reports analyst consensus EPS moves slowly, so a linear blend of EPS
    between anchors — re-derived to P/E off each day's actual price — tracks the
    daily price moves that drive most fwd-P/E variance, which a linear blend of
    PE itself would ignore. This is also what lets the monthly IBES anchors (each
    a dimensionless PRICE/MEANEST) ride the daily yfinance price: currency and
    split factors cancel because only the price *ratio* matters.

    A loss shows up as EPS <= 0 at a real anchor (negative stored P/E). We null
    that anchor and never interpolate a span that a loss bounds — the P/E is
    unstable through the whole neighborhood of EPS=0, not just at the crossing —
    so the line breaks from the last profitable anchor to the next one, and the
    positive anchors on either side still plot. Days outside the [first anchor,
    last anchor] window stay None — honest empty over fake extrapolation.
    Interpolated days get `flag_col: True`; real anchors False. Rows a loss leaves
    as a gap get `<value_col>_loss: True` (drives the chart's loss shading).
    """
    loss_col = value_col + "_loss"  # True on the gap rows a forecast loss creates
    for r in rows:
        r[flag_col] = False
        r[loss_col] = False
        if r.get(value_col) == 0:
            r[value_col] = None  # a 0 P/E is a degenerate placeholder, not a value
    # An anchor carries a real, priced P/E. `r.get(value_col)` is falsy for both
    # None and 0, so a zero P/E is skipped (its EPS would be infinite); a negative
    # P/E (forecast loss) is kept — its sign is what places the break.
    anchors = [
        i for i, r in enumerate(rows)
        if r.get(value_col) and r.get("price")
    ]
    eps_at = [rows[i]["price"] / rows[i][value_col] for i in anchors]
    for i, eps in zip(anchors, eps_at):
        if eps <= 0:
            rows[i][value_col] = None  # loss anchor: undefined P/E, serve as a gap
            rows[i][loss_col] = True
    for ai in range(len(anchors) - 1):
        L_i, R_i = anchors[ai], anchors[ai + 1]
        L_eps, R_eps = eps_at[ai], eps_at[ai + 1]
        if L_eps <= 0 or R_eps <= 0:  # a loss bounds this span -> gap; flag it
            for i in range(L_i, R_i + 1):
                if rows[i].get(value_col) is None:
                    rows[i][loss_col] = True
            continue
        L_ord = date.fromisoformat(rows[L_i]["date"]).toordinal()
        span = date.fromisoformat(rows[R_i]["date"]).toordinal() - L_ord
        if span <= 0:
            continue
        for i in range(L_i + 1, R_i):
            row = rows[i]
            if not row.get("price"):
                continue
            t = (date.fromisoformat(row["date"]).toordinal() - L_ord) / span
            row[value_col] = row["price"] / (L_eps + t * (R_eps - L_eps))
            row[flag_col] = True
    return rows


def _window_target(now_date: str, days: int | None, ytd: bool) -> str:
    """Snap date for the `then` endpoint: Jan 1 of `now`'s year (YTD) or
    now - `days`."""
    nd = date.fromisoformat(now_date)
    return (date(nd.year, 1, 1) if ytd else nd - timedelta(days=days)).isoformat()


def _delta_rows(db_path: str, ticker: str, days: int | None, ytd: bool) -> list[dict]:
    """Rows needed to compute a ticker's delta point, bounded to
    [latest forward_pe+price anchor on/before the window target, now] so we don't
    read and interpolate the full multi-year series on every request. That anchor
    is the left edge of the gap `then` falls in, so interpolating this slice
    matches interpolating the whole history. Falls back to the full series only
    when no priced anchor predates the target (thin/young tickers)."""
    now_date = storage.latest_value_date(db_path, ticker, "forward_pe")
    if now_date is None:
        return []
    target = _window_target(now_date, days, ytd)
    start = storage.latest_value_date(
        db_path, ticker, "forward_pe", on_or_before=target, require_price=True
    )
    return storage.read_history(db_path, ticker, start_date=start)


def _sparkline_series(values: list[float], n: int = 48) -> list[float]:
    """Evenly downsample to at most n points, always keeping the first and last
    so the sparkline endpoints match the then/now values."""
    if len(values) <= n:
        return values
    step = (len(values) - 1) / (n - 1)
    return [values[round(i * step)] for i in range(n)]


def _safe_div(a, b):
    return (a / b) if (a is not None and b) else None


def _decompose(then_price, now_price, then_pe, now_pe) -> dict:
    """Split the forward-P/E % change into price and EPS movement. Implied
    forward EPS = price / forward_pe, so P/E = price / EPS holds exactly and the
    contributions add up: price_contrib + eps_contrib == delta_pct. `*_change_pct`
    are each driver's raw move over the window; `*_contrib` are their exact share
    of ΔP/E%. Fields are None when a price or EPS is missing/zero."""
    then_eps = _safe_div(then_price, then_pe)
    now_eps = _safe_div(now_price, now_pe)
    out = {"then_eps": then_eps, "now_eps": now_eps,
           "price_change_pct": None, "eps_change_pct": None,
           "price_contrib": None, "eps_contrib": None}
    if not (then_price and now_price and then_eps and now_eps):
        return out
    rp = now_price / then_price
    reps = now_eps / then_eps
    out.update(price_change_pct=rp - 1, eps_change_pct=reps - 1,
               price_contrib=(rp - 1) / reps, eps_contrib=(1 / reps) - 1)
    return out


def _delta_point(rows: list[dict], days: int | None, ytd: bool) -> dict:
    """Forward-P/E change from `now` (latest value) to `then` (value on/before
    now - window). The live forward_pe series is sparse (live snapshots + a few
    backfilled anchors), so we interpolate it to daily exactly as the chart does
    before snapping — otherwise a 1-month delta could read off an anchor a year
    back. Live forward_pe only, never IBES. `*_interpolated` flags the endpoint
    as a filled value; `then` is None when the window predates coverage. `series`
    is the interpolated forward_pe over the window (then..now) for a sparkline;
    the price/EPS decomposition fields explain the move (see `_decompose`)."""
    rows = _interpolate_series(rows, "forward_pe", "forward_pe_interpolated")
    pts = [(r["date"], r["forward_pe"], r["forward_pe_interpolated"], r.get("price"))
           for r in rows if r.get("forward_pe") is not None]
    empty = {"now_date": None, "now": None, "now_interpolated": None,
             "then_date": None, "then": None, "then_interpolated": None,
             "delta": None, "delta_pct": None, "series": [],
             "then_price": None, "now_price": None, "then_eps": None, "now_eps": None,
             "price_change_pct": None, "eps_change_pct": None,
             "price_contrib": None, "eps_contrib": None}
    if not pts:
        return empty
    if rows[-1].get("forward_pe_loss"):  # latest fwd P/E is a forecast loss -> now is N/A,
        return {**empty, "now_date": rows[-1]["date"]}  # not the last pre-loss value
    now_date, now_val, now_interp, now_price = pts[-1]
    target = _window_target(now_date, days, ytd)
    then = next((p for p in reversed(pts) if p[0] <= target), None)
    window_start = then[0] if then else target
    series = _sparkline_series([v for d, v, *_ in pts if d >= window_start])
    if then is None:
        return {**empty, "now_date": now_date, "now": now_val,
                "now_interpolated": now_interp, "now_price": now_price,
                "now_eps": _safe_div(now_price, now_val), "series": series}
    then_date, then_val, then_interp, then_price = then
    delta = now_val - then_val
    return {"now_date": now_date, "now": now_val, "now_interpolated": now_interp,
            "then_date": then_date, "then": then_val, "then_interpolated": then_interp,
            "delta": delta, "delta_pct": (delta / then_val) if then_val else None,
            "series": series, "then_price": then_price, "now_price": now_price,
            **_decompose(then_price, now_price, then_val, now_val)}


def _parse_iso_date(s: str | None) -> str | None:
    """Validate `s` as an ISO date and return it canonicalised to YYYY-MM-DD, so
    basic-format (20240101) or week-date inputs still compare correctly against
    stored dates. None on invalid/empty input (treated as 'unspecified')."""
    if not s:
        return None
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        return None


@router.get("/", include_in_schema=False)
def dashboard(request: Request) -> HTMLResponse:
    cfg = _cfg()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "tickers": cfg["tickers"],
            "first_live_collection_date": cfg.get("first_live_collection_date"),
            "api_base": f"/{SLUG}/api",
            "delta_url": f"/{SLUG}/delta",
        },
    )


@router.get("/delta", include_in_schema=False)
def delta_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "delta.html",
        {
            "tickers": _cfg()["tickers"],
            "api_base": f"/{SLUG}/api",
            "home_url": f"/{SLUG}/",
        },
    )


@router.get("/api/tickers")
def api_tickers():
    return _cfg()["tickers"]


@router.get("/api/delta")
def api_delta(window: str = Query("1m")):
    """Per-ticker forward-P/E change over `window` (1d|1w|1m|3m|6m|1y|ytd),
    using live forward_pe only. `then` snaps to the most recent value on/before
    now - window; null when the window predates the ticker's coverage."""
    cfg = _cfg()
    w = (window or "1m").lower()
    ytd = w == "ytd"
    days = DELTA_WINDOWS.get(w)
    if not ytd and days is None:
        w, days = "1m", DELTA_WINDOWS["1m"]
    out = []
    for t in cfg["tickers"]:
        rows = _delta_rows(cfg["database_path"], t, days, ytd)
        name = next((r["name"] for r in reversed(rows) if r.get("name")), "")
        out.append({"ticker": t, "name": name, "window": w,
                    **_delta_point(rows, days, ytd)})
    return out


def _history_rows(
    db_path: str, ticker: str, start_date: str | None, end_date: str | None
) -> list[dict]:
    """Interpolated history for [start_date, end_date]. We read OUT to the real
    anchors (forward_pe and IBES, each priced) just before `start_date` and just
    after `end_date`, so interpolation has anchors on both sides of a window that
    lands inside a sparse gap, then clip back to the requested window. Otherwise a
    custom window between two anchors would render a blank P/E segment."""
    SERIES = ("forward_pe", "forward_pe_ibes")
    read_start, read_end = start_date, end_date
    if start_date:
        lefts = [storage.latest_value_date(db_path, ticker, col,
                                           on_or_before=start_date, require_price=True)
                 for col in SERIES]
        lefts = [a for a in lefts if a]
        if lefts:
            read_start = min(lefts + [start_date])
    if end_date:
        rights = [storage.earliest_value_date(db_path, ticker, col,
                                              on_or_after=end_date, require_price=True)
                  for col in SERIES]
        rights = [a for a in rights if a]
        if rights:
            read_end = max(rights + [end_date])
    rows = storage.read_history(db_path, ticker, start_date=read_start, end_date=read_end)
    rows = _interpolate_series(rows, "forward_pe", "forward_pe_interpolated")
    rows = _interpolate_series(rows, "forward_pe_ibes", "forward_pe_ibes_interpolated")
    if start_date:
        rows = [r for r in rows if r["date"] >= start_date]
    if end_date:
        rows = [r for r in rows if r["date"] <= end_date]
    return rows


@router.get("/api/history/{ticker}")
def api_history(
    ticker: str,
    start: str | None = None,
    end: str | None = None,
    range_: str = Query("all", alias="range"),
):
    """History endpoint. Window can be specified as either `start`/`end` (ISO,
    either optional, takes precedence) or a `range` preset
    (1m|3m|6m|1y|3y|5y|all). Rows are adaptively downsampled server-side."""
    cfg = _cfg()
    ticker = ticker.upper()
    if ticker not in cfg["tickers"]:
        raise HTTPException(status_code=404)

    start_date = _parse_iso_date(start)
    end_date = _parse_iso_date(end)
    if not start_date and not end_date:
        rng = (range_ or "all").lower()
        if rng not in RANGE_DAYS:
            rng = "all"
        days = RANGE_DAYS[rng]
        if days:
            start_date = (date.today() - timedelta(days=days)).isoformat()

    rows = _history_rows(cfg["database_path"], ticker, start_date, end_date)
    return _downsample(rows, _pick_bucket(len(rows)))


def _hide_nonpositive_pe(row: dict) -> dict:
    """A P/E is a meaningful multiple only when positive; a non-positive value (a
    trailing or forecast loss) reads as undefined. The chart enforces this via
    interpolation; the latest-snapshot grid enforces it here."""
    for c in ("ttm_pe", "forward_pe", "forward_pe_ibes"):
        if row.get(c) is not None and row[c] <= 0:
            row[c] = None
    return row


@router.get("/api/latest")
def api_latest():
    cfg = _cfg()
    rows = storage.latest_per_ticker(cfg["database_path"], cfg["tickers"])
    return [_hide_nonpositive_pe(r) for r in rows]


@router.post("/api/refresh")
def api_refresh():
    cfg = _cfg()
    try:
        scheduler.snapshot_all(cfg["tickers"], cfg["database_path"])
    except scheduler.Busy:
        raise HTTPException(status_code=409, detail="snapshot already in progress")
    return {"status": "ok"}


@contextmanager
def lifespan():
    storage.init_db(_cfg()["database_path"])
    yield


@contextmanager
def scheduler_lifespan():
    cfg = _cfg()
    sched = scheduler.start_scheduler(
        cfg["tickers"], cfg["database_path"], cfg["fetch_interval_seconds"]
    )
    try:
        yield
    finally:
        sched.shutdown(wait=False)
