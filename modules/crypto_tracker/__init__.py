"""Crypto Tracker module: exposes MODULE for the host to discover."""

from core.module import Module

from . import cache, views

MODULE = Module(
    slug=views.SLUG,
    name="Crypto Tracker",
    description="Multi-asset crypto portfolio: time- & money-weighted returns from on-chain flows.",
    router=views.router,
    order=120,
    icon="🪙",
    scheduler=cache.scheduler_lifespan,
)
