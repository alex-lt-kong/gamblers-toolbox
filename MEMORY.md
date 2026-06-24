# Project Memory

## Active Status

**Latest:** **Bloomberg-terminal UI overhaul** + rename to **Gambler's Terminal** on branch
`feat/bloomberg-terminal-theme` (off `main`, which now includes the merged Pyramiding Calculator,
PR #11). Shared `core/static/terminal.css` (black/amber/green-red, IBM Plex Mono bundled locally)
themes all pages; ag-grid + Chart.js recoloured, and charts are now Bloomberg *GP*-style (price
axis on the **right** + colored last-value tags; crosshair declined). 76 tests + Playwright pass;
committed locally, not pushed.

**Objective:** pe_monitor now handles money-losing companies correctly — forward-P/E
lines (live red + IBES green) **break** across forecast-loss windows instead of
bridging them or plotting a negative P/E. Done on branch `fix/pe-chart-gaps-and-ibes-neg`
(cut off `feat/pe-chart-enhancements`). Key invariant: forward-P/E columns store the raw
*signed* ratio; the "non-positive ⇒ undefined" rule lives at serve time
(`_interpolate_series` for charts/delta, `_hide_nonpositive_pe` for the latest grid).

**Immediate next steps:** Open the PR — chart work is Playwright-verified (breaks, time axis,
alignment, even ticks, loss bands) and the review findings are addressed (delta N/A on loss;
custom-range right-anchor; zero-P/E guard). Still open: guard chart history against stale
responses (transient wrong window on fast range-switching). Deferred to a follow-up PR:
gap-aware downsampling (loss gaps can vanish at coarse zoom), the explicit-series-state
refactor, and a Playwright/JS E2E test (covers the untested chart JS).

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
- Live E2E (Playwright): `npm i playwright` in `~/pwtest` + `npx playwright install chromium`;
  run `python3 -m core --config config.toml` on :9090 (token `demo-token-1234`, prod-copy DB).
  Drive: select tickers via `gridApi.forEachNode(n => n.setSelected(true))`, click
  `.range-btn[data-range=...]`, then read `chartInstances` scales (geometry/ticks) or
  screenshot `#chart-card-<T>`. Jinja auto-reloads dashboard.html (no app restart needed).
  npm registry reachable here; external UAT host is NOT (sandbox egress).

## Activity Log

### 2026-06-24 — Bloomberg-terminal theme + rename to "Gambler's Terminal" (branch `feat/bloomberg-terminal-theme`)
- Overhauled look/feel to mimic a Bloomberg Terminal. New shared `core/static/terminal.css`
  (served at `/static/terminal.css`, linked by every page): black canvas, amber chrome, green/red
  data, cyan functions/links, sharp corners, dense monospace. Palette centralized in CSS vars,
  reusing legacy `--bg/--surface/--ink/--muted/--border/--accent` names so the variable-driven
  `dashboard.html` recolored almost for free (data pages drop their inline `:root` colours).
- Pages: landing → "function menu" (amber header + live clock, amber function bar, numbered rows,
  cyan mnemonics, fixed bottom status bar); calculator + ai_ratios + delta + dashboard all themed.
- ag-grid `themeQuartz.withParams` → dark (amber headers, black rows) on both pe_monitor grids.
  Chart.js: TTM→amber, forward→red, IBES→green on black; dim grid/ticks via `Chart.defaults`;
  loss-band tints + cutoff line retinted; volume bars muted. Bloomberg *GP*-style conventions
  added: price axis moved to the **right** on both stacked charts (pe/vol stay pixel-aligned via
  `pinX` now padding *both* ends — moving y off the left un-pinned it), plus a `lastValueTagPlugin`
  drawing a colored latest-value chip per line over the right axis (vertically dodged when close).
  Crosshair declined by user. e2e alignment test still green.
- Font: bundled **IBM Plex Mono** locally (OFL, `core/static/fonts/*.woff2`, no CDN) as `--mono`
  + ag-grid `fontFamily` + `Chart.defaults.font.family`. Closest free face to the Terminal's
  institutional monospace (VT323/Share Tech Mono compared, rejected). Emoji fallbacks appended;
  sandbox has no emoji font so module icons (📈🗻🤖) tofu in screenshots only, fine in-browser.
- Rename **Gambler's Toolbox → Gambler's Terminal** across display strings only (FastAPI title,
  landing, README, manifest name/short_name + black theme colour, config-sample comments). Kept
  internal ids (slug `gamblers-toolbox`, env var `GAMBLERS_TOOLBOX_CONFIG`, log prefix) unchanged.
- Verified: 76 tests pass (incl. chart e2e geometry — recolor moved no pixels); Playwright drove
  all pages, `document.fonts.check` confirms IBM Plex Mono loaded, zero console errors. Local
  commit; not pushed, no PR.

### 2026-06-24 — Add Pyramiding Calculator module (branch `feat/avg-down-calculator`)
- New self-contained module `modules/averaging_calc/` (display name "Pyramiding Calculator",
  slug `averaging-calc`, icon 🗻 Mount Fuji — Unicode has no pyramid glyph): given a position
  (qty, avgCost, mktPx) and a target P/L%, returns the shares to add at market to move the %
  from its current level to the target. Landing card at order=20. Primary use: pressing a
  *winning* position — raise cost basis to dial a gaudy gain back (e.g. +20→+15); also averages
  down a loss. No data/scheduler/lifespan. README module table updated.
- Math (`calc.py`): `x = qty·(px − avgCost·(1+t)) / (px·t)`, i.e. new% = Q(P−C)/(QC+xP) =
  constant dollar P/L over a growing cost basis. P/L% only shrinks toward 0; reachable band is
  strictly between 0 and current%. Dollar P/L is unchanged by the buy.
- **Single source of truth (Approach A):** all math lives in `calc.py::evaluate()`; the page
  `calculator.html` just `fetch()`es `GET /api/calc` (debounced, race-guarded) and renders — no
  formula in the browser, so nothing can drift. `target_pct` is optional (current-only readout);
  an unreachable target is `200 {reachable:false, plan:null}`, not an error; whole-share figures
  are computed server-side too.
- **Hardening from an independent review (all verified):** (1) non-finite inputs `inf`/`nan`/
  `1e309` were returning HTTP **500** (Starlette `JSONResponse` uses `allow_nan=False`) — now
  `math.isfinite` validates all four inputs → **400**; (2) a `_require_finite_result` guard on
  *every* derived value — the top-level readout (`current_pnl_pct`/`pnl_amount`, which leaked
  `inf` for extreme finite inputs like `avg_cost=1e-308`) **and** the plan (incl. before
  `math.ceil`, which `OverflowError`s on `inf`) — so all overflow 400s, never 500; (3)
  `target==current` float knife-edge could emit a ~1e-12 *negative*
  share count — guarded by requiring `shares_to_buy > 0` before marking reachable.
- Verified: re-derived formula independently; 200k+50k random round-trips reproduce the target%
  to ~1e-13; monotonicity 0 violations; **Playwright** browser drive renders correctly with no
  console errors. `tests/test_averaging_calc.py` (18) + `test_discovery_order_and_unique_slugs`
  fix. **76 tests pass.** Pushed; PR not yet opened.

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
  every null is a genuine gap). `cutoffLine` maps date→pixel via the scale.
- Verified the axis math via **headless Chart.js** (node + stubbed canvas): line/bar data map
  to exact axis pixels. Caught+fixed there: bar charts default `offset:true`, insetting the
  volume bars to ~83% of the width while the lines use the full axis (looked like vol not
  matching the lines / data "squished") — forced `offset:false` on both x-axes.
- Stood up local **Playwright** E2E (headless Chromium + app on :9090). It caught what the
  headless axis-math missed: the volume x-axis reserves a right gutter for its last date
  label that the label-less P/E axis didn't → ~29px misalignment (pe_plot 1023 vs vol 994).
  Fixed with `pinX` (afterFit `paddingRight=36`) on both → both `[58,992]`. Also added even
  round-date ticks (`niceDateTicks` + `fmtDateTick`) and cleared the review findings (2 stale
  comments, single-point-extent guard). All Playwright-verified (geometry, ticks, no errors).
- **Loss shading** (`lossBandsPlugin`): a semi-transparent band tinted to each line over the
  periods its P/E is undefined (a missing line otherwise reads as a glitch). TTM: client-side,
  null P/E within trailing-EPS coverage (reaches edges; day-gaps in trailing-EPS don't
  fragment it). Forward/IBES: a server loss flag `<col>_loss` from `_interpolate_series` — the
  client can't tell a forecast-loss null from a no-data null at the *visible edge* (e.g. MU's
  IBES loss starts before its first in-window positive anchor). Sub-3-week gaps dropped as
  interp noise. Playwright-verified: INTC blue-only, MU blue+green, NIO blue+red+green.
- Review findings fixed: delta `now` → N/A when the latest forward P/E is a loss (was a stale
  pre-loss value); `_history_rows` reaches forward to the right anchor (new
  `storage.earliest_value_date`) so a custom window inside a sparse gap interpolates instead
  of rendering blank (verified on MU 2021-08: 0 → 5 values); a stored 0 P/E is nulled (was
  plotting y:0). +3 tests (36 total). Deferred to a follow-up PR: gap-aware downsampling,
  explicit-series-state refactor, Playwright/JS E2E.

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

_(Older entries moved to `MEMORY_ARCHIVE.md`.)_
