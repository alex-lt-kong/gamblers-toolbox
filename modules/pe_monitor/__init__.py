"""P/E Monitor module: exposes MODULE for the host to discover."""

from pathlib import Path

from core.module import Module

from . import views

_HERE = Path(__file__).resolve().parent

MODULE = Module(
    slug=views.SLUG,
    name="P/E Monitor",
    description="Live forward & TTM P/E dashboard for a watchlist of equities.",
    router=views.router,
    order=10,
    icon="📈",
    static_dir=str(_HERE / "static"),
    static_name="pe_monitor_static",
    on_startup=views.start_background,
    on_shutdown=views.stop_background,
)
