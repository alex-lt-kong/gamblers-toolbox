# Project Memory — Archive

Older activity-log entries pruned from `MEMORY.md` (newest first).

### 2026-06-22 — Review unified FastAPI branch
- Reviewed the complete `main...origin/feat/unified-fastapi-landing` diff; the
  19-test suite hangs on its first request with the currently resolved unbounded
  FastAPI/Starlette/httpx dependency set.
- Found scheduler-disabled replicas skip P/E database initialization/migrations;
  global scheduler ownership makes app lifespans non-reentrant.
- Found AI-ratios timeout leaves running worker threads behind and URL query-token
  authentication exposes bearer secrets to normal request logging.
- Noted strict config/date validation and stale module-example docs as cleanup work.

### 2026-06-22 — Rename project to Gambler's Toolbox
- Renamed branding to **Gambler's Toolbox** / slug `gamblers-toolbox`: FastAPI title,
  landing page, manifest, icon, README, config comments, log banner prefix.
- Renamed env vars `MARKET_UTILS_CONFIG`→`GAMBLERS_TOOLBOX_CONFIG`,
  `MARKET_UTILS_LOG_SECRETS`→`GAMBLERS_TOOLBOX_LOG_SECRETS` (breaking for external
  launchers; update systemd/launch scripts). Tests updated to match.
- GitHub repo renamed `market-monitors`→`gamblers-toolbox`; updated the `origin` URL.
- Left the local working dir name and a stale notebook path string as-is.

### 2026-06-22 — Refactor: factory, typed config, tests, scheduler flag
- Added a pytest+TestClient integration suite (`tests/`): config/secret validation, auth
  on/off + revocation, discovery + duplicate-slug rejection, prefixed routes, 409
  concurrent refresh, lifecycle, schedulers-disabled.
- `build_app(config, modules)` is now a pure factory with no import-time config/DB side
  effects; uvicorn uses `--factory core.main:create_app`; pe_monitor config is lazy.
- Module `on_startup`/`on_shutdown` replaced by a per-module `lifespan` context manager.
- Typed `HostConfig` (Pydantic); startup validates unique slugs + static mount names.
- `enable_schedulers` host flag (default on) to run background jobs on one instance only.

### 2026-06-22 — Security & robustness hardening (review follow-up)
- Secrets masked in the startup banner (opt-in via `GAMBLERS_TOOLBOX_LOG_SECRETS`).
- Refuse to start when `auth_tokens` set but `secret_key` is default/empty/short.
- Sessions store the token hash and revalidate each request, so removing a token revokes
  its cookies; session `max_age` set to 7 days.
- Clear "copy config.sample.toml" errors; README documents per-module config + upgrade
  data migration.
- ai_ratios: coverage threshold (keep last-good below 95%), single-flight refresh (409),
  Wikipedia timeout + bounded Yahoo deadline.
- Lifecycle: schedulers started synchronously + retained; both modules have on_shutdown.

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
