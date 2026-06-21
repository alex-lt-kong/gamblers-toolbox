"""AI Ratios module: exposes MODULE for the host to discover."""

from core.module import Module

from . import cache, views

MODULE = Module(
    slug=views.SLUG,
    name="AI Ratios",
    description="Share of the S&P 500 attributable to AI exposure, refreshed on a schedule.",
    router=views.router,
    icon="🤖",
    on_startup=cache.start,
    on_shutdown=cache.stop,
)
