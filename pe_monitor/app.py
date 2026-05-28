"""Flask app: serves the dashboard and JSON API, runs the background crawler."""

from datetime import date, timedelta

from flask import Flask, abort, jsonify, render_template, request

import config
import scheduler
import storage

CONFIG = config.load_config()
storage.init_db(CONFIG["database_path"])

app = Flask(__name__)


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


def _interpolate_forward_pe(rows: list[dict]) -> list[dict]:
    """Fill NULL forward_pe values between two real anchors by interpolating
    implied forward EPS, then deriving fwd P/E from each day's actual price.

    Why this works: at any anchor date d we know both Price(d) and FwdPE(d)
    from real data, so ForwardEPS(d) = Price(d) / FwdPE(d) is determined.
    Between earnings reports, analyst consensus EPS moves slowly (typically
    1-3% per quarter), so a linear blend of EPS between anchors is a much
    better assumption than a linear blend of FwdPE itself — the latter
    would ignore the daily price movements that drive most fwd-P/E variance
    between earnings.

    Each gap row gets `forward_pe_interpolated: True`; real rows get False.
    Days outside the [first anchor, last anchor] window stay NULL — better
    honest empty than fake-confident extrapolation off the edges.
    """
    for r in rows:
        r["forward_pe_interpolated"] = False
    anchors = [
        i for i, r in enumerate(rows)
        if r.get("forward_pe") is not None and r.get("price")
    ]
    if len(anchors) < 2:
        return rows
    for ai in range(len(anchors) - 1):
        L_i, R_i = anchors[ai], anchors[ai + 1]
        if R_i == L_i + 1:
            continue
        L, R = rows[L_i], rows[R_i]
        L_eps = L["price"] / L["forward_pe"]
        R_eps = R["price"] / R["forward_pe"]
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
            row["forward_pe"] = row["price"] / eps
            row["forward_pe_interpolated"] = True
    return rows


@app.route("/")
def dashboard():
    return render_template(
        "dashboard.html",
        tickers=CONFIG["tickers"],
        first_live_collection_date=CONFIG.get("first_live_collection_date"),
    )


@app.route("/api/tickers")
def api_tickers():
    return jsonify(CONFIG["tickers"])


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


@app.route("/api/history/<ticker>")
def api_history(ticker: str):
    """History endpoint. Window can be specified as either:
      - `?start=YYYY-MM-DD&end=YYYY-MM-DD` (either bound optional), or
      - `?range=1m|3m|6m|1y|3y|5y|all` (preset).
    `start`/`end` take precedence over `range`. Rows are adaptively
    downsampled server-side to stay under TARGET_POINTS."""
    ticker = ticker.upper()
    if ticker not in CONFIG["tickers"]:
        abort(404)

    start_date = _parse_iso_date(request.args.get("start"))
    end_date = _parse_iso_date(request.args.get("end"))
    if not start_date and not end_date:
        rng = request.args.get("range", "all").lower()
        if rng not in RANGE_DAYS:
            rng = "all"
        days = RANGE_DAYS[rng]
        if days:
            start_date = (date.today() - timedelta(days=days)).isoformat()

    rows = storage.read_history(
        CONFIG["database_path"], ticker,
        start_date=start_date, end_date=end_date,
    )
    rows = _interpolate_forward_pe(rows)
    return jsonify(_downsample(rows, _pick_bucket(len(rows))))


@app.route("/api/latest")
def api_latest():
    return jsonify(storage.latest_per_ticker(CONFIG["database_path"], CONFIG["tickers"]))


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    scheduler.snapshot_all(CONFIG["tickers"], CONFIG["database_path"])
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    scheduler.start_scheduler(
        CONFIG["tickers"],
        CONFIG["database_path"],
        CONFIG["fetch_interval_seconds"],
    )
    app.run(host=CONFIG["host"], port=CONFIG["port"])
