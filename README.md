# Gambler's Toolbox

One FastAPI app that serves a landing page and mounts each market tool as a
self-contained **module** — single port, shared auth, automatic Swagger docs.

| Module      | URL           | What it does                                       |
|-------------|---------------|----------------------------------------------------|
| P/E Monitor | `/pe-monitor` | Live forward & TTM P/E dashboard for a watchlist.  |
| AI Ratios   | `/ai-ratios`  | Share of the S&P 500 attributable to AI exposure.  |

Landing `/` · docs at `/docs`, `/redoc`, `/openapi.json`.

## Run

```bash
pip install -r requirements.txt
cp config.sample.toml config.toml                                        # host config
cp modules/pe_monitor/config.sample.toml modules/pe_monitor/config.toml  # per-module
python -m core --config config.toml
```

`--config` is mandatory (also read from `GAMBLERS_TOOLBOX_CONFIG`, e.g. for
`uvicorn --factory core.main:create_app`). The host config holds only the shared
concerns — `host`, `port`, `secret_key`, `auth_tokens`, `enable_schedulers`; each
module keeps its own. Schedulers run in-process, so run a **single worker** (or
set `enable_schedulers = false` on all but one replica).

## Authentication

Off by default (`auth_tokens = []`). To enable, set a strong `secret_key` (else
the app refuses to start) plus one or more token UUIDs:

```toml
secret_key = "…"   # python -c "import secrets; print(secrets.token_urlsafe(32))"
auth_tokens = ["3f9b1c2e-..."]
```

Open any page once with `?token=<uuid>` to receive a signed session cookie;
requests without it get `401`.

## Adding a module

Drop a package under `modules/` exposing a `MODULE` constant; the host discovers
it and mounts its router at `/<slug>`. Data, templates, and schedulers stay
private to the module.

```python
# modules/my_tool/__init__.py
from core.module import Module
from . import views                 # views.router is an APIRouter

MODULE = Module(
    slug="my-tool", name="My Tool", description="One line for the landing card.",
    router=views.router,
    lifespan=...,    # optional CM: resource setup (e.g. init the DB)
    scheduler=...,   # optional CM: background jobs (skipped if disabled)
    static_dir=...,  # optional: served at /my-tool/static
)
```

## Tests

```bash
pip install -r requirements-dev.txt && python -m pytest
```
