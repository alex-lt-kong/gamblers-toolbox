"""FastAPI router for the P/E Monitor module: dashboard + JSON API.

Was app.py. The host (core/main.py) mounts this router at /pe-monitor and calls
start_background() on startup to run the crawler.
"""

from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from . import config, scheduler, storage

SLUG = "pe-monitor"
_HERE = Path(__file__).resolve().parent

CONFIG = config.load_config()
storage.init_db(CONFIG["database_path"])

router = APIRouter()
templates = Jinja2Templates(directory=str(_HERE / "templates"))


# Predefined ranges for /api/history. None = no lower bound (all).
RANGE_DAYS = {
    "1m": 31, "3m": 92, "6m": 183,
    "1y": 366, "3y": 3 * 366, "5y": 5 * 366,
    "all": None,
}

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
    """Fill NULL `value_col` (a forward-P/E series) between two real anchors by
    interpolating implied forward EPS, then deriving the P/E from each day's
    actual price.

    Why this works: at any anchor date d we know both Price(d) and PE(d) from
    real data, so ForwardEPS(d) = Price(d) / PE(d) is determined. Between
    earnings reports, analyst consensus EPS moves slowly (typically 1-3% per
    quarter), so a linear blend of EPS between anchors is a much better
    assumption than a linear blend of PE itself — the latter would ignore the
    daily price movements that drive most fwd-P/E variance between earnings.

    This is also what makes the IBES line work from monthly anchors: each
    anchor stores a dimensionless IBES P/E (PRICE/MEANEST), and the daily
    yfinance price supplies the between-anchor motion — currency and split
    factors cancel because only the price *ratio* matters here.

    Each gap row gets `flag_col: True`; real rows get False. Days outside the
    [first anchor, last anchor] window stay NULL — better honest empty than
    fake-confident extrapolation off the edges.
    """
    for r in rows:
        r[flag_col] = False
    anchors = [
        i for i, r in enumerate(rows)
        if r.get(value_col) is not None and r.get("price")
    ]
    if len(anchors) < 2:
        return rows
    for ai in range(len(anchors) - 1):
        L_i, R_i = anchors[ai], anchors[ai + 1]
        if R_i == L_i + 1:
            continue
        L, R = rows[L_i], rows[R_i]
        L_eps = L["price"] / L[value_col]
        R_eps = R["price"] / R[value_col]
        L_ord = date.fromisoformat(L["date"]).toordinal()
        span = date.fromisoformat(R["date"]).toordinal() - L_ord
        if span <= 0:
            continue
        for i in range(L_i + 1, R_i):
            row = rows[i]
            if not row.get("price"):
                continue
            t = (date.fromisoformat(row["date"]).toordinal() - L_ord) / span
            eps = L_eps + t * (R_eps - L_eps)
            if eps <= 0:
                continue  # EPS sign change — linear blend isn't meaningful
            row[value_col] = row["price"] / eps
            row[flag_col] = True
    return rows


def _parse_iso_date(s: str | None) -> str | None:
    """Validate `s` as ISO YYYY-MM-DD. Returns the string on success, None
    otherwise (invalid input is treated as 'unspecified')."""
    if not s:
        return None
    try:
        date.fromisoformat(s)
        return s
    except ValueError:
        return None


@router.get("/", include_in_schema=False)
def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "tickers": CONFIG["tickers"],
            "first_live_collection_date": CONFIG.get("first_live_collection_date"),
            "api_base": f"/{SLUG}/api",
        },
    )


@router.get("/api/tickers")
def api_tickers():
    return CONFIG["tickers"]


@router.get("/api/history/{ticker}")
def api_history(
    ticker: str,
    start: str | None = None,
    end: str | None = None,
    range_: str = Query("all", alias="range"),
):
    """History endpoint. Window can be specified as either
    `start`/`end` (ISO, either optional, takes precedence) or a `range` preset
    (1m|3m|6m|1y|3y|5y|all). Rows are adaptively downsampled server-side."""
    ticker = ticker.upper()
    if ticker not in CONFIG["tickers"]:
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

    rows = storage.read_history(
        CONFIG["database_path"], ticker,
        start_date=start_date, end_date=end_date,
    )
    rows = _interpolate_series(rows, "forward_pe", "forward_pe_interpolated")
    rows = _interpolate_series(rows, "forward_pe_ibes", "forward_pe_ibes_interpolated")
    return _downsample(rows, _pick_bucket(len(rows)))


@router.get("/api/latest")
def api_latest():
    return storage.latest_per_ticker(CONFIG["database_path"], CONFIG["tickers"])


@router.post("/api/refresh")
def api_refresh():
    scheduler.snapshot_all(CONFIG["tickers"], CONFIG["database_path"])
    return {"status": "ok"}


def start_background() -> None:
    scheduler.start_scheduler(
        CONFIG["tickers"],
        CONFIG["database_path"],
        CONFIG["fetch_interval_seconds"],
    )
