# Project Memory

## Active Status

**Objective:** market-utils is now a single FastAPI app (`core/`) that auto-discovers
plugin **modules** under `modules/` and serves them behind one landing page, one port,
and one shared auth layer. Each module exposes a `MODULE` descriptor (`core/module.py`)
and keeps its own data, templates, and scheduler.

- `core/` — host shell: `module.py` (interface), `registry.py` (discovery), `auth.py`
  (token→cookie gate), `config.py`, `main.py` (`build_app` + lifespan), `__main__.py`.
- `modules/pe_monitor/` — P/E dashboard (was a standalone Flask app). `views.py` holds the
  APIRouter; `backfill/` tools still run standalone via the `_bootstrap` sys.path shim.
- `modules/ai_ratios/` — S&P AI-exposure ratio; computes via `core.py`, caches in `cache.py`
  with its own scheduler; `views.py` serves dashboard + JSON API.

**Run:** `pip install -r requirements.txt` then `python -m core`. Auth is off until
`auth_tokens` are set in `config.toml`.

**Next steps / ideas:**
- Auth is built but default-disabled; the user plans to turn it on with real UUID tokens.
- Consider exempting `/healthz` (and optionally `/docs`) when auth is enabled.
- ai_ratios initial compute needs network (Yahoo/Wikipedia); it runs in a background thread
  so it never blocks startup, and retries on its schedule.

## Activity Log

### 2026-06-22 — Unify pe_monitor + ai_ratios under one FastAPI app
- Built `core/` host shell with a pluggable `Module` interface and auto-discovery of
  `modules/*` exposing `MODULE`.
- Moved `pe_monitor/` → `modules/pe_monitor/` (git mv; backfill `_bootstrap` unaffected).
  Converted `app.py` → `views.py` (APIRouter); made `scheduler.py` imports relative;
  fixed dashboard `url_for(path=)`, prefixed client `fetch` calls and the manifest.
- Built `modules/ai_ratios/` from the old `ai_ratios.py` CLI: split compute (`core.py`),
  added cache + own scheduler (`cache.py`), router + Jinja dashboard. Dropped the CLI.
- Added shared auth (`SessionMiddleware` + token gate), default-disabled.
- Consolidated dependencies into a single top-level `requirements.txt` (FastAPI/uvicorn;
  dropped Flask). Verified end-to-end: landing, both modules, static, `/docs` (tagged per
  module), auth on/off, and backfill imports.
