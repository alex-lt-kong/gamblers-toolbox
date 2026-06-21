"""Host app: discovers modules, mounts each under its slug, serves the landing page.

The only things unified here are the interface, the port, and authentication.
Each module brings its own routes, static, templates and scheduler.
"""

import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from core import config as host_config
from core.auth import make_auth_gate
from core.registry import discover_modules

_HERE = Path(__file__).resolve().parent
MODULES = discover_modules()
CONFIG = host_config.load_config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run startup hooks off the event loop: a module's initial data fetch must
    # not block the server (or other modules) from coming up.
    for m in MODULES:
        if m.on_startup:
            threading.Thread(target=m.on_startup, name=f"startup:{m.slug}", daemon=True).start()
    yield
    for m in MODULES:
        if m.on_shutdown:
            m.on_shutdown()


def build_app() -> FastAPI:
    app = FastAPI(title="market-utils", lifespan=lifespan)

    # Add the gate first, then SessionMiddleware last so it sits outermost and
    # request.session is populated before the gate reads it.
    app.middleware("http")(make_auth_gate(CONFIG["auth_tokens"]))
    app.add_middleware(SessionMiddleware, secret_key=CONFIG["secret_key"])

    templates = Jinja2Templates(directory=str(_HERE / "templates"))

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

    return app


app = build_app()
