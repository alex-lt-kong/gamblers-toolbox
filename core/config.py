"""Host config: bind address, cookie secret, and shared auth tokens.

Falls back to safe defaults (auth disabled) when config.toml is absent so the
app runs out of the box. Module-specific config stays inside each module.
"""

import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

_DEFAULTS = {
    "host": "127.0.0.1",
    "port": 8000,
    "secret_key": "dev-insecure-change-me",
    "auth_tokens": [],
}


def load_config(path: str = "config.toml") -> dict:
    cfg = dict(_DEFAULTS)
    fp = _ROOT / path
    if fp.exists():
        with open(fp, "rb") as f:
            cfg.update(tomllib.load(f))
    return cfg
