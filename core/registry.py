"""Discover modules: any subpackage of modules/ exposing a `MODULE` descriptor."""

import importlib
import pkgutil

import modules

from core.module import Module


def discover_modules() -> list[Module]:
    found: list[Module] = []
    for info in pkgutil.iter_modules(modules.__path__, modules.__name__ + "."):
        pkg = importlib.import_module(info.name)
        candidate = getattr(pkg, "MODULE", None)
        if isinstance(candidate, Module):
            found.append(candidate)
    found.sort(key=lambda m: (m.order, m.name))
    return found
