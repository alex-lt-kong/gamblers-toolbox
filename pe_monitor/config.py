"""Load and resolve project config. Shared between app.py and backfill/ tools."""

import tomllib
from pathlib import Path


def load_config(path: str = "config.toml") -> dict:
    base = Path(__file__).parent
    with open(base / path, "rb") as f:
        cfg = tomllib.load(f)
    cfg["database_path"] = str(base / cfg["database_path"])
    return cfg
