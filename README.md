# market-utils

A unified home for small market tools. One FastAPI app serves a landing page and
mounts each tool as a self-contained **module**, on a single port with shared
authentication and automatic Swagger docs.

## Modules

| Module       | URL           | What it does                                              |
|--------------|---------------|----------------------------------------------------------|
| P/E Monitor  | `/pe-monitor` | Live forward & TTM P/E dashboard for a watchlist.         |
| AI Ratios    | `/ai-ratios`  | Share of the S&P 500 attributable to AI exposure.        |

- Landing page: `/`
- Swagger / OpenAPI: `/docs`, `/redoc`, `/openapi.json`

## Run

```bash
pip install -r requirements.txt

# host config (bind / secret / tokens)
cp config.sample.toml config.toml      # required: --config has no default

# per-module config (each module loads its own)
cp modules/pe_monitor/config.sample.toml modules/pe_monitor/config.toml

python -m core --config config.toml
```

`--config` is mandatory; the path is also honored via the `MARKET_UTILS_CONFIG`
env var (handy for systemd / direct uvicorn):

```bash
MARKET_UTILS_CONFIG=config.toml uvicorn --factory core.main:create_app --host 0.0.0.0 --port 9090
```

The host config holds only the shared concerns — `host`, `port`, `secret_key`,
`auth_tokens`, and `enable_schedulers`. Each module keeps its own config inside its
package. On startup the
app logs the loaded config path, bind address, and discovered modules (token and
secret values are masked unless `MARKET_UTILS_LOG_SECRETS=1`).

> **Upgrading from the standalone `pe_monitor/` layout?** Its `config.toml` and
> `pe_history.db`/data are gitignored, so `git pull` won't relocate them. Move them
> into the new path once:
> `mv pe_monitor/config.toml pe_monitor/pe_history.db* pe_monitor/pe_history modules/pe_monitor/`

> **Scaling:** schedulers run in-process — run a **single worker**. For multiple
> replicas, set `enable_schedulers = false` on all but one so background jobs (and
> DB writes) happen exactly once.

## Authentication

Disabled by default (`auth_tokens = []` → open). To enable, add one or more UUIDs
**and** a strong `secret_key` — the app refuses to start otherwise, since the
default key is public and would let anyone forge a session cookie:

```toml
secret_key = "…"   # python -c "import secrets; print(secrets.token_urlsafe(32))"
auth_tokens = ["3f9b1c2e-..."]
```

Then open any page once with `?token=<uuid>`; the server sets a signed session
cookie and redirects to the clean URL. Requests without a valid cookie get `401`.

## Adding a module

Drop a package under `modules/` that exposes a `MODULE` constant. The host
discovers it automatically and mounts its router at `/<slug>`.

```python
# modules/my_tool/__init__.py
from core.module import Module
from . import views          # views.router is an APIRouter

MODULE = Module(
    slug="my-tool",
    name="My Tool",
    description="One line for the landing card.",
    router=views.router,
    on_startup=...,           # optional: start the module's own scheduler
    static_dir=...,           # optional: served at /my-tool/static
)
```

The only things unified across modules are the interface (`core/module.py`), the
port, and authentication. Data, templates, and schedulers stay private to each
module. JSON API routes appear in Swagger automatically; HTML pages opt out with
`include_in_schema=False`.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Integration tests (FastAPI `TestClient`) cover config validation + secret rejection,
module discovery, auth on/off + revocation, prefixed routes, concurrent-refresh `409`,
and lifecycle startup/shutdown.
