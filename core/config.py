"""Host config: bind address, cookie secret, and shared auth tokens.

The config file is mandatory and chosen explicitly via `python -m core --config
<path>` (which sets MARKET_UTILS_CONFIG). Defaults only fill in keys the file
omits — an unspecified or missing file is a hard error. Module-specific config
stays inside each module.
"""

import os
import tomllib
from pathlib import Path

_ENV_VAR = "MARKET_UTILS_CONFIG"

_DEFAULTS = {
    "host": "127.0.0.1",
    "port": 9090,
    "secret_key": "dev-insecure-change-me",
    "auth_tokens": [],
}


def config_source(path: str | None = None) -> str:
    chosen = path or os.environ.get(_ENV_VAR)
    if not chosen:
        raise RuntimeError(
            f"No host config specified. Run `python -m core --config <path>` "
            f"or set {_ENV_VAR}."
        )
    return chosen


def load_config(path: str | None = None) -> dict:
    fp = Path(config_source(path))
    if not fp.exists():
        raise FileNotFoundError(f"Host config not found: {fp}")
    cfg = dict(_DEFAULTS)
    with open(fp, "rb") as f:
        cfg.update(tomllib.load(f))
    return cfg
