"""Host app: discovers modules, mounts each under its slug, serves the landing page.

The only things unified here are the interface, the port, and authentication.
Each module brings its own routes, static, templates and scheduler.
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from core import config as host_config
from core.auth import make_auth_gate
from core.registry import discover_modules

_HERE = Path(__file__).resolve().parent
MODULES = discover_modules()
CONFIG = host_config.load_config()
SESSION_MAX_AGE = 7 * 24 * 60 * 60  # 7 days


def _log_config() -> None:
    show = os.environ.get("MARKET_UTILS_LOG_SECRETS", "").lower() in ("1", "true", "yes")
    tokens = CONFIG["auth_tokens"]
    secret_disp = CONFIG["secret_key"] if show else ("set" if CONFIG["secret_key"] else "unset")
    if show:
        tokens_disp = tokens or "[]  (auth DISABLED)"
    else:
        tokens_disp = f"{len(tokens)} configured" if tokens else "none (auth DISABLED)"
    print(f"[market-utils] config:      {host_config.config_source()}")
    print(f"[market-utils] bind:        {CONFIG['host']}:{CONFIG['port']}")
    print(f"[market-utils] secret_key:  {secret_disp}")
    print(f"[market-utils] auth_tokens: {tokens_disp}")
    print(f"[market-utils] modules:     {', '.join(m.slug for m in MODULES) or '(none)'}")
    if tokens and not show:
        print("[market-utils] (set MARKET_UTILS_LOG_SECRETS=1 to print token/secret values)")


_log_config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup hooks just start each module's scheduler (fast); the slow initial
    # fetch runs as a one-off job inside that scheduler, so boot isn't blocked.
    for m in MODULES:
        if m.on_startup:
            m.on_startup()
    yield
    for m in MODULES:
        if m.on_shutdown:
            m.on_shutdown()


def build_app() -> FastAPI:
    app = FastAPI(title="market-utils", lifespan=lifespan)

    # Add the gate first, then SessionMiddleware last so it sits outermost and
    # request.session is populated before the gate reads it.
    app.middleware("http")(make_auth_gate(CONFIG["auth_tokens"]))
    app.add_middleware(SessionMiddleware, secret_key=CONFIG["secret_key"], max_age=SESSION_MAX_AGE)

    templates = Jinja2Templates(directory=str(_HERE / "templates"))
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")

    for m in MODULES:
        app.include_router(m.router, prefix=f"/{m.slug}", tags=[m.name])
        if m.static_dir:
            app.mount(
                f"/{m.slug}/static",
                StaticFiles(directory=m.static_dir),
                name=m.static_name or f"{m.slug}_static",
            )

    @app.get("/", include_in_schema=False)
    def landing(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "landing.html", {"modules": MODULES})

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse(_HERE / "static" / "favicon.ico")

    return app


app = build_app()
