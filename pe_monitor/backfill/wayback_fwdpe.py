"""One-shot scraper: pull historical Forward P/E from Wayback Machine
snapshots of Yahoo Finance's /quote/<TICKER>/key-statistics page.

Why this exists: Yahoo only exposes the *current* forward P/E. Backfilling
the historical series requires either a paid data vendor (I/B/E/S, Zacks,
FactSet) or scraping archived snapshots. The Internet Archive doesn't
bot-block datacenter IPs, so this works from any host.

Two layouts are handled:
- Old (pre-2024 Svelte redesign): single `forwardPE` value embedded in a
  JSON blob in the page. One datapoint per snapshot, dated as the snapshot
  capture date.
- New (2024+): a table where each row is a metric and columns are
  ["Current", "<Q-end date 1>", ... "<Q-end date 5>"]. So each snapshot
  yields up to six datapoints — one "live" at snapshot date, five at the
  prior quarter-end dates. Snapshots taken weeks apart will overlap on the
  quarter-end values, which acts as a built-in consistency check.

Usage (from pe_monitor/):
    python backfill/wayback_fwdpe.py NVDA          # prints (date, fwd_pe) rows
    python backfill/wayback_fwdpe.py NVDA --json   # JSON output for the importer
"""

import argparse
import gzip
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from bs4 import BeautifulSoup

import _bootstrap  # noqa: F401  (sys.path shim)
import config


WAYBACK_URL_PREFIX = "https://web.archive.org/web/{ts}id_/{url}"
CDX_URL = "https://web.archive.org/cdx/search/cdx"
UA = "Mozilla/5.0 (compatible; PEHistorianBackfill/0.1)"


def cdx_snapshots(ticker: str, since: str = "20200101") -> list[tuple[str, str]]:
    """Return [(timestamp, original_url), ...] for one-snapshot-per-day,
    status 200 only, since the given YYYYMMDD."""
    target = f"finance.yahoo.com/quote/{ticker}/key-statistics"
    params = {
        "url": target,
        "output": "json",
        "from": since,
        "to": datetime.now().strftime("%Y%m%d"),
        "filter": "statuscode:200",
        "collapse": "timestamp:8",  # one per unique YYYYMMDD prefix
    }
    full = f"{CDX_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(full, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        rows = json.load(r)
    if not rows:
        return []
    # First row is header [urlkey, timestamp, original, mimetype, statuscode, digest, length]
    return [(row[1], row[2]) for row in rows[1:]]


def fetch_wayback(timestamp: str, url: str) -> str:
    """Fetch the raw archived page (no Wayback toolbar) and decode it."""
    full = WAYBACK_URL_PREFIX.format(ts=timestamp, url=url)
    req = urllib.request.Request(full, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
    return data.decode("utf-8", errors="replace")


# Adaptive backoff: tracked across calls in scrape_ticker via a closure. IA
# throttles per-IP, so a single failure usually means everything will fail
# for a while. We sleep exponentially in the failure streak (30s, 60s, 120s,
# ...) up to MAX_BACKOFF, and reset on the first success. Caps the per-snap
# attempts so a hopelessly-stuck snapshot doesn't pin the whole run.
MAX_RETRIES_PER_SNAPSHOT = 4
BACKOFF_BASE_SECONDS = 30
MAX_BACKOFF_SECONDS = 900  # 15 min — past this, we're just guessing


def parse_new_layout(html: str, snapshot_date: str) -> dict[str, float]:
    """Parse the post-2024 Svelte table.

    Locates the row containing 'Forward P/E' and pairs each cell with the
    column-header date. The first column header is 'Current' — we map that
    to the snapshot capture date (YYYY-MM-DD).

    Returns {iso_date: fwd_pe}. Empty if the table isn't found.
    """
    soup = BeautifulSoup(html, "lxml")
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if not cells or "Forward P/E" not in cells[0].get_text():
            continue
        # Found the row. Walk up to the table and find its header.
        table = tr.find_parent("table")
        if not table:
            return {}
        header_dates = None
        for hr in (table.find("thead") or table).find_all("tr"):
            ths = [t.get_text(strip=True) for t in hr.find_all(["th", "td"])]
            if any("/" in c or c == "Current" for c in ths):
                header_dates = ths
                break
        if not header_dates:
            return {}
        values = [c.get_text(strip=True) for c in cells]
        out: dict[str, float] = {}
        for label, val_text in zip(header_dates, values):
            if not val_text or val_text in ("Forward P/E", "--", "N/A", ""):
                continue
            try:
                val = float(val_text.replace(",", ""))
            except ValueError:
                continue
            if label == "Current":
                date_iso = snapshot_date
            else:
                # Header is like "7/31/2025"
                try:
                    dt = datetime.strptime(label, "%m/%d/%Y")
                except ValueError:
                    continue
                date_iso = dt.strftime("%Y-%m-%d")
            out[date_iso] = val
        return out
    return {}


_OLD_FWDPE_RE = re.compile(r'"forwardPE"\s*:\s*\{\s*"raw"\s*:\s*([-\d.]+)')


def parse_old_layout(html: str, snapshot_date: str) -> dict[str, float]:
    """Parse the pre-2024 React layout. Returns a single datapoint dated
    as the snapshot capture date."""
    m = _OLD_FWDPE_RE.search(html)
    if not m:
        return {}
    try:
        return {snapshot_date: float(m.group(1))}
    except ValueError:
        return {}


def _fetch_with_retries(ts: str, url: str, failure_streak: list[int],
                        verbose: bool) -> str | None:
    """Wrap fetch_wayback with adaptive backoff. `failure_streak` is a
    single-element list used as a mutable counter shared with the caller —
    crude but avoids globals. Returns None if the snapshot is unfetchable
    after MAX_RETRIES_PER_SNAPSHOT attempts."""
    for attempt in range(MAX_RETRIES_PER_SNAPSHOT):
        try:
            html = fetch_wayback(ts, url)
            failure_streak[0] = 0  # any success resets the streak
            return html
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            failure_streak[0] += 1
            wait = min(BACKOFF_BASE_SECONDS * (2 ** (failure_streak[0] - 1)),
                       MAX_BACKOFF_SECONDS)
            if verbose:
                print(f"    fetch attempt {attempt+1} failed ({e}); "
                      f"streak={failure_streak[0]}, sleeping {wait}s",
                      file=sys.stderr)
            if attempt < MAX_RETRIES_PER_SNAPSHOT - 1:
                time.sleep(wait)
    return None


def scrape_ticker(
    ticker: str,
    snapshots: list[tuple[str, str]],
    sleep_s: float,
    verbose: bool,
) -> dict[str, float]:
    """Walk every snapshot for the ticker, parse fwd P/E, merge results.

    Conflict policy: if multiple snapshots report different values for the
    same quarter-end date, the *first* one encountered wins (snapshots are
    chronological, so this prefers the value as it was reported nearest in
    time to the actual quarter-end — less revised).
    """
    if verbose:
        print(f"[{ticker}] scraping {len(snapshots)} snapshots", file=sys.stderr)

    merged: dict[str, float] = {}
    failure_streak = [0]  # mutable container so _fetch_with_retries can update it
    skipped = 0
    for ts, url in snapshots:
        snap_date = f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}"
        html = _fetch_with_retries(ts, url, failure_streak, verbose)
        if html is None:
            skipped += 1
            if verbose:
                print(f"  {snap_date} SKIPPED after {MAX_RETRIES_PER_SNAPSHOT} attempts",
                      file=sys.stderr)
            continue
        parsed_new = parse_new_layout(html, snap_date)
        parsed = parsed_new or parse_old_layout(html, snap_date)
        new_dates = [d for d in parsed if d not in merged]
        for d in new_dates:
            merged[d] = parsed[d]
        if verbose:
            layout = "new" if parsed_new else ("old" if parsed else "none")
            print(f"  {snap_date} [{layout}]: {len(parsed)} parsed, {len(new_dates)} new",
                  file=sys.stderr)
        time.sleep(sleep_s)
    if verbose and skipped:
        print(f"[{ticker}] {skipped} snapshots skipped after retries", file=sys.stderr)
    return merged


def _emit_single(ticker: str, data: dict[str, float], as_json: bool) -> None:
    rows = sorted(data.items())  # by date
    if as_json:
        print(json.dumps([{"date": d, "forward_pe": v} for d, v in rows], indent=2))
    else:
        for d, v in rows:
            print(f"{d}\t{v:.2f}")


def run_all(args: argparse.Namespace) -> None:
    """Batch mode. For each ticker in config.toml: pre-flight CDX count,
    skip-with-warning if below the threshold, otherwise scrape and write
    `<TICKER>.json` into the output directory. International tickers
    (.HK/.KS/.SS/.KQ) typically have <5 snapshots so they get skipped here
    by the same threshold without needing a separate suffix rule.
    """
    import pathlib  # local: only needed in batch mode

    cfg = config.load_config()
    tickers = [t.upper() for t in cfg["tickers"]]
    out_dir = pathlib.Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    scraped, skipped_resume, skipped_coverage, failed = [], [], [], []
    for ticker in tickers:
        out_file = out_dir / f"{ticker}.json"
        if args.resume and out_file.exists():
            print(f"[{ticker}] skip — output exists ({out_file.name})", file=sys.stderr)
            skipped_resume.append(ticker)
            continue

        try:
            snaps = cdx_snapshots(ticker, args.since)
        except Exception as e:
            print(f"[{ticker}] ERR: CDX lookup failed: {e}", file=sys.stderr)
            failed.append(ticker)
            continue

        if len(snaps) < args.min_snapshots:
            print(f"[{ticker}] WARN: only {len(snaps)} snapshots since "
                  f"{args.since} (< --min-snapshots={args.min_snapshots}); "
                  f"skipping. Common for HK/KR/SS tickers — Yahoo doesn't "
                  f"archive them densely on the Wayback Machine.",
                  file=sys.stderr)
            skipped_coverage.append(ticker)
            continue

        try:
            data = scrape_ticker(ticker, snaps, args.sleep, not args.quiet)
        except Exception as e:
            print(f"[{ticker}] ERR during scrape: {e}", file=sys.stderr)
            failed.append(ticker)
            continue

        rows = [{"date": d, "forward_pe": v} for d, v in sorted(data.items())]
        out_file.write_text(json.dumps(rows, indent=2))
        print(f"[{ticker}] wrote {len(rows)} rows -> {out_file.name}", file=sys.stderr)
        scraped.append(ticker)

    print(f"\n=== Summary ===", file=sys.stderr)
    print(f"  Scraped:                  {len(scraped):>3}  {scraped}", file=sys.stderr)
    print(f"  Skipped (resume/exists):  {len(skipped_resume):>3}  {skipped_resume}", file=sys.stderr)
    print(f"  Skipped (low coverage):   {len(skipped_coverage):>3}  {skipped_coverage}", file=sys.stderr)
    print(f"  Failed:                   {len(failed):>3}  {failed}", file=sys.stderr)
    print(f"  Output dir: {out_dir}", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ticker", nargs="?",
                    help="Single ticker to scrape (omit when using --all)")
    ap.add_argument("--all", action="store_true",
                    help="Batch mode: scrape every ticker in config.toml")
    ap.add_argument("--out", default="wayback_out",
                    help="Output directory for --all mode (default: ./wayback_out)")
    ap.add_argument("--resume", action="store_true",
                    help="In --all mode, skip tickers whose output file already exists")
    ap.add_argument("--min-snapshots", type=int, default=5,
                    help="In --all mode, skip tickers with fewer than this "
                         "many CDX snapshots (default 5). Filters out HK/KR/SS "
                         "names which typically have 0-3 snapshots.")
    ap.add_argument("--since", default="20200101", help="YYYYMMDD lower bound")
    ap.add_argument("--sleep", type=float, default=10.0,
                    help="Seconds between Wayback fetches (default 10.0). "
                         "IA throttles ~17 quick requests/min; with 10s the "
                         "adaptive backoff rarely triggers, runs are slow "
                         "but reliable.")
    ap.add_argument("--json", action="store_true",
                    help="Single-ticker mode: emit JSON instead of TSV")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args()

    if args.all and args.ticker:
        ap.error("pass either a ticker positional or --all, not both")
    if not args.all and not args.ticker:
        ap.error("pass a ticker, or use --all to scrape every ticker in config.toml")

    if args.all:
        run_all(args)
    else:
        ticker = args.ticker.upper()
        snaps = cdx_snapshots(ticker, args.since)
        if not args.quiet:
            print(f"[{ticker}] {len(snaps)} snapshots since {args.since}",
                  file=sys.stderr)
        data = scrape_ticker(ticker, snaps, args.sleep, not args.quiet)
        _emit_single(ticker, data, args.json)


if __name__ == "__main__":
    main()
