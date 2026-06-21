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
cp config.sample.toml config.toml      # required: --config has no default
python -m core --config config.toml
```

`--config` is mandatory; the path is also honored via the `MARKET_UTILS_CONFIG`
env var (handy for systemd / direct uvicorn):

```bash
MARKET_UTILS_CONFIG=config.toml uvicorn core.main:app --host 0.0.0.0 --port 9090
```

The host config holds only the shared concerns — `host`, `port`, `secret_key`, and
`auth_tokens`. Each module keeps its own config inside its package. On startup the
app logs the loaded config path, bind address, tokens, and discovered modules.

## Authentication

Disabled by default (`auth_tokens = []` → open). To enable, add one or more UUIDs:

```toml
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
