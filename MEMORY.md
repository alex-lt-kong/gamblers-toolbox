# Project Memory

## Active Status

**Objective:** pe_monitor now handles money-losing companies correctly — forward-P/E
lines (live red + IBES green) **break** across forecast-loss windows instead of
bridging them or plotting a negative P/E. Done on branch `fix/pe-chart-gaps-and-ibes-neg`
(cut off `feat/pe-chart-enhancements`). Key invariant: forward-P/E columns store the raw
*signed* ratio; the "non-positive ⇒ undefined" rule lives at serve time
(`_interpolate_series` for charts/delta, `_hide_nonpositive_pe` for the latest grid).

**Immediate next steps:** Browser-verify on UAT two things: (1) loss-gap breaks on all
three lines, and (2) the new **time-proportional x-axis** — multi-ticker (e.g. INTC+ARM on
"All") must no longer crush old years. Then open a PR. Still open from the
`feat/pe-chart-enhancements` review: guard chart history against stale responses, and fetch
a pre-window anchor before interpolating custom ranges. (DB backup already deleted.)

- `core/` — host shell: `module.py` (interface), `registry.py` (discovery), `auth.py`
  (token→cookie gate), typed `config.py` (Pydantic `HostConfig`), `main.py`
  (`build_app(config, modules)` factory + `create_app`), `__main__.py`. Tests in `tests/`.
- `modules/pe_monitor/` — P/E dashboard (was a standalone Flask app). `views.py` holds the
  APIRouter; `backfill/` tools still run standalone via the `_bootstrap` sys.path shim.
- `modules/ai_ratios/` — S&P AI-exposure ratio; computes via `core.py`, caches in `cache.py`
  with its own scheduler; `views.py` serves dashboard + JSON API.

**Run:** `pip install -r requirements.txt` then `python -m core --config config.toml`
(`--config` is mandatory; default bind 9090). Auth is off until `auth_tokens` are set;
when enabled a strong `secret_key` is required or the app refuses to start.

**Parked (not planned — unnecessary for the current single-process deploy):**
ai_ratios JSON-snapshot persistence; an exempt `/healthz` endpoint.

**Notes:**
- `Module` now has two hooks: `lifespan` (resource setup, e.g. DB init — runs on every
  instance) and `scheduler` (background jobs — skipped when `enable_schedulers=false`).
  Each hook owns a local scheduler instance (no module globals).
- `build_app(config, modules)` has no import-time side effects; uvicorn runs it via
  `--factory core.main:create_app`. Run schedulers on one instance only.
- Refreshes are single-flight; ai_ratios keeps last-known-good below 95% coverage.
- Tests: `pip install -r requirements-dev.txt && python -m pytest` (21 integration tests).

## Activity Log

### 2026-06-23 — Forward-P/E money-losing handling (Design Y, branch `fix/pe-chart-gaps-and-ibes-neg`)
- Problem: a company forecast to lose money has negative forward EPS ⇒ forward P/E is
  undefined. Three write-sites stored a *negative* P/E (live `fetcher` via Yahoo
  `forwardPE`; `import_wayback_fwdpe`; `import_ibes` PRICE/MEANEST) that plotted below
  zero, and `_interpolate_series` *bridged* the loss gaps, faking a smooth trend.
- Design Y — store signed, break at serve: forward-P/E columns keep the raw *signed* ratio
  (negative = forecast loss); no source guards, no schema change. `_interpolate_series`
  reworked in EPS-space — nulls loss anchors and never interpolates a span a loss bounds,
  so the line breaks from last-profitable to next-profitable anchor (kills the near-zero
  +∞ interpolation spike: MU max served 115.7 vs a 2887 spike). TTM stays nulled-at-source
  (it isn't interpolated). `_hide_nonpositive_pe` enforces the rule on the latest grid.
- Chart `dashboard.html`: a `segment.borderColor` callback breaks a line only at *genuine*
  gaps (row present, value null) while still bridging *alignment* gaps (ticker lacks a union
  date) — both are `null` and `spanGaps` bridged both before. NOTE: first impl had a dead
  loop — Chart.js emits one segment per *adjacent* pair (p1DataIndex = p0DataIndex+1), so
  scanning indices *between* the endpoints never fired and no line broke (caught visually on
  UAT, not by tests). Fixed to test the segment's two endpoints against the genuine-gap mask.
- An earlier "drop negatives at source + null the DB" attempt was reverted in favour of Y.
  Verified: 33 tests pass (+`test_interpolate_breaks_across_forward_loss`); MU IBES breaks
  Jul–Sep 2023; 0 negatives served across all 39 tickers; NIO → 98 positive fwd-P/E days.
- Follow-up refactor (same branch): replaced the categorical *union-date* x-axis with a
  shared **linear time axis** (x = epoch-ms). The union made per-ticker density distort time
  — equal calendar spans rendered at unequal width (INTC 1986→ at 30d buckets vs ARM daily ⇒
  recent years ~15× wider). This deletes `unionDates`/`alignRows`/`col`/`genuineGap`, so
  `segmentBreak` is gone too — replaced by plain `spanGaps:false` (one ticker per chart ⇒
  every null is a genuine gap). `cutoffLine` maps date→pixel via the scale. NOT yet
  browser-verified here (no headless render); Python tests only confirm the template renders.

### 2026-06-23 — Review `feat/pe-chart-enhancements`
- Compared the fetched feature ref against `origin/main` (3 commits; 4 files).
- Found overlapping history requests can populate/render the cache with an obsolete
  range after the user has selected a newer range.
- Found history is clipped to a custom start before interpolation, so a window starting
  inside a sparse forward-P/E gap loses values until the next in-window anchor.
- Refactoring opportunities: share the duplicated column chooser and replace the
  categorical union-date axis with a true time axis.

### 2026-06-22 — Review fixes on `feat/delta-fwd-pe` (sort, config hardening, delta perf)
- Reviewed the whole project; fixed four items, each its own commit:
  1. Delta page sorted wrong — the `delta_pct` column's `sort: "desc"` overrode the JS
     magnitude sort. Dropped it so default order is |Δ%|-desc (header still sortable).
  2. `HostConfig` now `extra="forbid"` — a typo'd `auth_token` no longer silently
     disables auth.
  3. `build_app` calls `check_secret` (renamed from `_check_secret`) so a weak secret +
     auth is rejected regardless of construction path, not just via `load_config`.
  4. `api_delta` read the full per-ticker history (back to 1986, ~10k rows × 39) every
     request. New `storage.latest_value_date` + `views._delta_rows` bound the read to
     `[latest forward_pe+price anchor <= window target, now]`; provably identical to the
     full interpolation (full-read fallback when no priced anchor predates target).
     ~29x faster for 1M, ~5x for 1Y/YTD. `_window_target` shared by `_delta_rows`/`_delta_point`.
- Tests: +`test_unknown_key_rejected`, +`test_build_app_rejects_weak_secret_with_auth`,
  +`test_bounded_read_matches_full_history` (6 tickers × 4 windows). 30 pass.
- Also trimmed README 103→65 lines.

### 2026-06-22 — Add Δ-forward-P/E page to pe_monitor (branch `feat/delta-fwd-pe`)
- New page `/pe-monitor/delta` + `delta.html`: per-ticker forward-P/E change over a
  selectable window (1D/1W/1M/3M/6M/YTD/1Y), ag-grid leaderboard sorted by |Δ%|, nav
  links both ways. No schema change.
- `GET /pe-monitor/api/delta?window=` and `_delta_point` in `views.py`. Live `forward_pe`
  only (no IBES). Critical: the raw live series is sparse, so it's interpolated to daily
  (same `_interpolate_series` as the chart) **before** snapping `then` to now−window —
  otherwise a 1-month delta snaps to an anchor a year back. Interpolated endpoints are
  flagged (`≈`); `then` is null when the window predates coverage.
- `tests/test_delta.py` (6 tests): window/snap/interp logic + endpoint shape/fallback. 27 pass.
- Branch `feat/delta-fwd-pe`, rebased onto `main` after PR #6 (unified-fastapi-landing)
  merged; pushed for its own PR.

### 2026-06-22 — Fix review bugs #1, #5, dates; pin dependency set (#2)
- Split `Module` into `lifespan` (always-run resource setup) + `scheduler` (gated by
  `enable_schedulers`); `core/main.py` enters lifespans on every instance and schedulers
  only when enabled — so a scheduler-disabled replica still runs `init_db` (#1).
- Both module schedulers now live in a local variable inside their `scheduler` CM; the
  module-global `_scheduler` (and ai_ratios `start`/`stop`) are gone, so concurrent app
  instances no longer clobber each other (#5).
- `_parse_iso_date` canonicalises via `.isoformat()` so basic-format/week-date inputs no
  longer break SQLite lexical date comparisons.
- Tests: rewrote lifecycle tests (instance capture + a schedulers-disabled-still-inits
  guard), added `_parse_iso_date` unit tests; README module example updated. 21 pass.
- #2: pinned requirements to the tested set (compatible-release `~=`), incl. starlette.
  Stayed on `httpx` for TestClient (the deprecation points at an unverified `httpx2`
  package — sandbox flagged it as supply-chain risk); silenced the warning in `pytest.ini`.

_(Older entries moved to `MEMORY_ARCHIVE.md`.)_
