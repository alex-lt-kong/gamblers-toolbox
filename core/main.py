"""Host app factory: discovers modules, mounts each under its slug, serves the
landing page.

The only things unified here are the interface, the port, and authentication.
Each module brings its own routes, static, templates and scheduler. `build_app`
is a pure factory (no import-time work); `create_app` is the zero-arg entry that
uvicorn calls via `--factory`.
"""

import os
from contextlib import ExitStack, asynccontextmanager
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
SESSION_MAX_AGE = 7 * 24 * 60 * 60  # 7 days


def _log_config(config: host_config.HostConfig, modules: list) -> None:
    show = os.environ.get("MARKET_UTILS_LOG_SECRETS", "").lower() in ("1", "true", "yes")
    tokens = config.auth_tokens
    secret_disp = config.secret_key if show else ("set" if config.secret_key else "unset")
    if show:
        tokens_disp = tokens or "[]  (auth DISABLED)"
    else:
        tokens_disp = f"{len(tokens)} configured" if tokens else "none (auth DISABLED)"
    print(f"[market-utils] config:      {host_config.config_source()}")
    print(f"[market-utils] bind:        {config.host}:{config.port}")
    print(f"[market-utils] secret_key:  {secret_disp}")
    print(f"[market-utils] auth_tokens: {tokens_disp}")
    print(f"[market-utils] modules:     {', '.join(m.slug for m in modules) or '(none)'}")
    if tokens and not show:
        print("[market-utils] (set MARKET_UTILS_LOG_SECRETS=1 to print token/secret values)")


def _validate_modules(modules: list) -> None:
    slugs = [m.slug for m in modules]
    dupes = sorted({s for s in slugs if slugs.count(s) > 1})
    if dupes:
        raise RuntimeError(f"duplicate module slugs: {dupes}")
    names = [m.static_name or f"{m.slug}_static" for m in modules if m.static_dir]
    dupe_names = sorted({n for n in names if names.count(n) > 1})
    if dupe_names:
        raise RuntimeError(f"duplicate static mount names: {dupe_names}")


def build_app(config: host_config.HostConfig, modules: list) -> FastAPI:
    _validate_modules(modules)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Each module owns its resources via its own context manager; they start
        # synchronously here (fast) and are torn down on shutdown in reverse.
        with ExitStack() as stack:
            for m in modules:
                if m.lifespan is not None:
                    stack.enter_context(m.lifespan())
            yield

    app = FastAPI(title="market-utils", lifespan=lifespan)

    # Gate first, then SessionMiddleware last so it sits outermost and
    # request.session is populated before the gate reads it.
    app.middleware("http")(make_auth_gate(config.auth_tokens))
    app.add_middleware(SessionMiddleware, secret_key=config.secret_key, max_age=SESSION_MAX_AGE)

    templates = Jinja2Templates(directory=str(_HERE / "templates"))
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")

    for m in modules:
        app.include_router(m.router, prefix=f"/{m.slug}", tags=[m.name])
        if m.static_dir:
            app.mount(
                f"/{m.slug}/static",
                StaticFiles(directory=m.static_dir),
                name=m.static_name or f"{m.slug}_static",
            )

    @app.get("/", include_in_schema=False)
    def landing(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "landing.html", {"modules": modules})

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse(_HERE / "static" / "favicon.ico")

    return app


def create_app() -> FastAPI:
    """Zero-arg factory for uvicorn (`uvicorn --factory core.main:create_app`)."""
    config = host_config.load_config()
    modules = discover_modules()
    _log_config(config, modules)
    return build_app(config, modules)
