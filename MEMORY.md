# Project Memory

## Active Status

**Latest:** Two review rounds done on `feat/crypto-tracker`, all fixes in `twr.py`. Round 1 (manual):
log-rate-space XIRR bracket (short-window gains resolve), strict CSV validation with line numbers
(real date / known asset / finite delta), atomic+validated price cache, injectable `compute(prices,
today)`. Round 2 (workflow code-review, `ccdd905`) cleared 7 confirmed correctness findings: (1) XIRR
now expands **both** brackets — a >99.99% loss resolves instead of `n/a` (round 1 only fixed the upper
side); (2) `historical_price` rejects a `/klines` candle whose openTime is a later day (a date before
the pair listed silently took the listing-day close); (3) `today` is **UTC** (`datetime.now(utc)`), not
host-local; (4) `load_portfolio` rejects future-dated rows; (5) and oversell (per-asset end-of-date
balance going negative), both with line numbers; (6) closed positions with float dust are dropped from
holdings (`abs(bal) < 1e-9`); (7) CLI `main()` catches Binance/runtime errors. Plus batched
`current_prices`. **112 tests pass** (+9). The P/E split-cliff finding is the **already-deferred**
event-triggered item below — different module, out of scope. Deferred follow-ups: batch historical
klines (cold-cache speed), cumulative-balance timeline, and the cross-module scheduler/cache dedup.

**Deferred (event-triggered, not scheduled):** a stock split will put a fake cliff in the
price panel because daily snapshots store raw `currentPrice` (`fetcher.py:46`) and are never
back-adjusted — only the `auto_adjust=True` backfill is. P/E lines are split-invariant so they
stay smooth, making price spuriously diverge. Fix WHEN the first split lands: read `yt.splits`
at crawl time, back-adjust prior stored `price`/`volume`, migrate existing rows. No split column
in `storage.py` today; no `yt.splits` read anywhere.

**Objective:** Ship the `crypto-tracker` module on `feat/crypto-tracker`. Because it branches off the
still-unmerged `feat/bloomberg-terminal-theme` (for `terminal.css`), merge order is bloomberg first,
then crypto-tracker — or rebase crypto-tracker onto `main` once bloomberg lands.

**Immediate next steps:** Push the fix commit and open the PR (base `feat/bloomberg-terminal-theme`,
or rebase onto `main` once that lands). **Binance egress is unverified in this sandbox** — the live
`compute()`/scheduler path is still unexercised; verify a real refresh against `api.binance.com` before
relying on the dashboard. Pre-existing TestClient hang remains intermittent (full suite passed here).

- `core/` — host shell: `module.py` (interface), `registry.py` (discovery), `auth.py`
  (token→cookie gate), typed `config.py` (Pydantic `HostConfig`), `main.py`
  (`build_app(config, modules)` factory + `create_app`), `__main__.py`. Tests in `tests/`.
- `modules/pe_monitor/` — P/E dashboard (was a standalone Flask app). `views.py` holds the
  APIRouter; `backfill/` tools still run standalone via the `_bootstrap` sys.path shim.
- `modules/ai_ratios/` — S&P AI-exposure ratio; computes via `core.py`, caches in `cache.py`
  with its own scheduler; `views.py` serves dashboard + JSON API.
- `modules/crypto_tracker/` — crypto portfolio TWR/MWR/CAGR. `twr.py` (math + `compute()`),
  `cache.py` (in-memory cache + 15-min scheduler), `views.py` (dashboard + JSON API). Reads
  `portfolio.csv` (gitignored; `portfolio.sample.csv` committed); disk price cache `.price_cache.json`.

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
- Live E2E (Playwright): `npm i playwright` in `~/pwtest` + `npx playwright install chromium`;
  run `python3 -m core --config config.toml` on :9090 (token `demo-token-1234`, prod-copy DB).
  Drive: select tickers via `gridApi.forEachNode(n => n.setSelected(true))`, click
  `.range-btn[data-range=...]`, then read `chartInstances` scales (geometry/ticks) or
  screenshot `#chart-card-<T>`. Jinja auto-reloads dashboard.html (no app restart needed).
  npm registry reachable here; external UAT host is NOT (sandbox egress).

## Activity Log

### 2026-06-27 — Crypto-tracker workflow code-review fixes, round 2 (branch `feat/crypto-tracker`, `ccdd905`)
- Ran the workflow-backed `/code-review` (8 finder angles → 27 candidates → 10 confirmed after an
  independent verifier per finding; 13 refuted, mostly real-but-non-bug duplication). All 10 in `twr.py`.
- Fixed 7 correctness findings: (1) **XIRR lower bracket** — round 1 only expanded the upper bound, so a
  >99.99% loss (root below `r=-0.9999`) still returned `n/a`; now expands whichever side the sign says
  (xnpv is monotone-decreasing). (2) **Pre-listing kline** — `/klines` returns the first candle at/after
  `startTime`, so a pre-listing date silently took the listing-day close; now rejects when `openTime`
  lands on a later UTC day. (3) **UTC `today`** — was `date.today()` (host-local) while all pricing is
  UTC, dropping same-day flows for hours on UTC-behind hosts. (4) **Future-dated** and (5) **oversell**
  (per-asset end-of-date balance < 0) rows now rejected with line numbers — closing the loader's own
  silent-drop / negative-holdings gaps. (6) **Float-dust** closed positions dropped from holdings
  (`abs(bal) < 1e-9`). (7) CLI `main()` catches `RuntimeError`/`requests` errors, not just `ValueError`.
- Efficiency: `current_prices` batches all tickers into one request. Deferred (logged): ranged
  historical-kline batch (cold-cache latency), cumulative-balance timeline (O(n²) scan, negligible at
  ~20 rows), and extracting the thrice-duplicated single-flight scheduler into `core` (cross-module).
- +9 tests (incl. near-total-loss XIRR, future/oversell rejection with a same-day net-≥0 false-positive
  guard, dust drop, mocked pre-listing/same-day kline, batched ticker, CLI error path). **112 pass.**

### 2026-06-27 — Crypto-tracker review fixes (branch `feat/crypto-tracker`)
- **P1 — IRR bracket:** `xirr` bisected in `[-0.9999, 1000]`, whose upper bound represents only a
  76.4% 30-day gain, so a 30-day double (annual IRR ≈ 4,597) found no sign change → MWR `n/a`. Rewrote
  it to bisect in **log-rate space** (`x = ln(1+r)`) with a dynamic cap (`min(80, 690/t_max)`) that
  keeps `exp(x)` and `(1+r)**t` below float overflow, expanding the upper bound until bracketed. A
  30-day double now yields +100% MWR. Regression tests added.
- **P1 — CSV validation:** `load_portfolio()` skipped malformed date/asset rows silently and accepted
  NaN/Inf deltas (which propagate to `total_value` and 500 on Starlette's `allow_nan=False` JSON). Now
  it validates each non-blank row (real `strptime` date — `DATE_RE` only checks shape; known asset;
  finite delta) and **raises with `line N` numbers**, surfaced via the cache's `last_error`. Blank
  rows still skipped.
- **Robustness — price cache:** `save_cache` writes a temp file + `os.replace` (atomic); `load_cache`
  treats a truncated/corrupt JSON as empty instead of raising on every future refresh.
- **Refactor — injectable `compute()`:** added a `BinancePrices` provider (`.price(symbol, date)`) and
  `compute(prices=None, today=None)`; valuation helpers take the provider instead of
  `(today_str, today_prices, cache)`. Enables a deterministic end-to-end `compute()` test (no network/disk).
- **Deferred / not in scope:** the P/E price-panel split-cliff (re-raised in review) stays the
  event-triggered item in Active Status (different module, pre-existing). Per-date valuation series +
  timestamp-format dedup left as follow-ups (negligible at current scale).
- 103 tests pass (+10 in `test_crypto_tracker.py`). `git diff --check` clean; module compiles.

### 2026-06-26 — Review `feat/crypto-tracker` against `main`
- Fetched and reviewed the full 9-commit branch, including the inherited terminal-theme/P/E chart
  changes. Reproduced a concrete MWR failure: a 30-day doubled position returns `None`, because the
  annualized XIRR bracket ends at 1,000× (only a 76.4% 30-day cumulative gain fits below that cap).
- Found that CSV parsing silently drops malformed date/asset rows and accepts non-finite deltas, so a
  typo can publish a materially incorrect portfolio or later make the JSON API fail. Also noted the
  price panel inherits stored raw live prices while backfill is adjusted, producing a false split cliff
  after a split unless persisted history is back-adjusted.
- Refactor candidates: build one per-date valuation series instead of repeatedly scanning rows for
  every range/boundary; inject a price provider/clock for deterministic compute tests; use atomic,
  validated disk-cache writes; extract the duplicated terminal timestamp formatting.
- Verified `git diff --check`, bytecode compilation, and 11 network-free crypto math tests. Did not
  change production code.

### 2026-06-26 — Add Crypto Tracker module (branch `feat/crypto-tracker`)
- New self-contained module `modules/crypto_tracker/` (4th; "Crypto Tracker", slug `crypto-tracker`,
  icon 🪙, `order=120`). Ports the standalone `twr.py` Binance CLI into the host architecture without
  changing its return math; assets in `config.ASSET_SYMBOLS` (BTC/ETH), 15-min refresh. Branched off
  `feat/bloomberg-terminal-theme` for `terminal.css`.
- `twr.py`: kept the original pure functions (`balance_at`, `xirr`/`xnpv`, `twr_over_range`,
  `mwr_over_range`, `annualize_cumul`) and added `compute()` → `{computed_at, as_of, total_value,
  holdings[], ranges[]}`; `main()` still prints standalone via `python -m modules.crypto_tracker.twr`.
  `load_portfolio()` returns `[]` when the CSV is absent.
- `cache.py`/`views.py` mirror `ai_ratios`: single-flight in-memory cache, `BackgroundScheduler`
  (interval + one-off initial), `GET /api/data`, `POST /api/refresh` (409 when busy). A failed
  refresh keeps last-known-good and sets `last_error`, so Binance being unreachable degrades to a
  warning rather than an error page.
- Dashboard (`templates/dashboard.html`, terminal-themed): holdings table + sign-colored
  TWR/MWR/CAGR table; renders a "no data yet" state from the empty cache (200 before first compute,
  like ai_ratios). Jinja `pct` macro for n/a/up/down; money via `{:,.2f}`.
- Data: `portfolio.csv` **gitignored** (real holdings local-only — user's call), seeded by committed
  `portfolio.sample.csv`; `.price_cache.json` gitignored (immutable klines). README table + Run steps
  updated. No host-config change; `requests` already pinned.
- Tests: `tests/test_crypto_tracker.py` (17) — TWR doubles on a single deposit; TWR≈0 vs MWR>0 on a
  flat-price dip-buy (the time- vs money-weighted distinction); XIRR recovers 10%; CAGR n/a under a
  year; populated-template render; refresh single-flight (409) + keeps-last-good on failure. Updated
  `test_app.py` discovery-order/landing/openapi-tag assertions for the 4th module. **93 tests pass.**
  Network-free — the live Binance `compute()` path was NOT exercised here (sandbox egress).

### 2026-06-25 — Price panel + per-panel toggles on pe-monitor charts (branch `feat/bloomberg-terminal-theme`)
- Added a third stacked panel (cyan `#3bc9ff` line) above P/E, reusing the existing
  `pinX`/`Y_AXIS_WIDTH`/`offset:false` machinery so price, P/E and volume share one date axis.
  Front-end only; `price`/`currency` already in each history row. Commit `531aaea`.
- Rationale: P/E = price/EPS, so price diverging from TTM P/E is the visual tell of an EPS
  revision. Chose a separate panel over a dual y-axis (avoids the arbitrary-scale trap and the
  right-pinned-axis layout from `581d9f7`).
- Refined (`0416547`): panel 110px→240px and a **logarithmic** price y-axis (price is always
  positive, so log is safe; P/E stays linear because it can go negative/null). Equal % moves now
  read as equal height, fixing the price-vs-P/E amplitude mismatch the short linear panel caused.
- Added three independent global **panel toggles** (Price / P/E / Volume) that show/hide each
  panel across all ticker charts via `hide-*` classes on `section.charts`; persisted in
  `localStorage`. Distinct from the TTM/Forward/IBES legend, which toggles lines inside the P/E panel.
- Extended `test_chart_e2e.py`: price panel exists and aligns with P/E + volume; Volume toggle hides panels.
- Split handling deliberately deferred until the first split occurs (see Active Status).
- Review pass `34179f3` — four findings fixed: (1) P1 font license — added verbatim SIL OFL 1.1
  at `core/static/fonts/OFL.txt` beside the `.woff2` files (canonical text; upstream fetch only
  paraphrased, so worth a re-check). (2) Hiding Volume stripped every date label (only Volume
  showed the x axis) — date labels now follow `bottomVisiblePanel()` via a shared `dateX(key)`;
  toggles re-render through new `redrawCharts()`. (3) Value tags could pile up at the plot bottom —
  added an upward bounce pass after the downward spacing pass. (4) Mouse-only chips — shared
  `makeChip()` gives series + panel chips `role=button`/`tabindex`/`aria-pressed` + Enter/Space.
- e2e now guards the date-axis re-homing (Volume visible→owns axis; hidden→labels move to P/E) and
  chip a11y attributes. No deterministic test for the tag-overlap case (tag pixel positions aren't exposed).

_(Older entries moved to `MEMORY_ARCHIVE.md`.)_
