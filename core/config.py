"""Host config: bind address, cookie secret, and shared auth tokens.

A typed (Pydantic) model, loaded from a mandatory TOML chosen via
`python -m core --config <path>` (or MARKET_UTILS_CONFIG). When auth is enabled
the secret_key must be strong, otherwise the signed session cookie is forgeable.
Module-specific config stays inside each module.
"""

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel

_ENV_VAR = "MARKET_UTILS_CONFIG"
_DEFAULT_SECRET = "dev-insecure-change-me"
_MIN_SECRET_LEN = 16


class HostConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 9090
    secret_key: str = _DEFAULT_SECRET
    auth_tokens: list[str] = []


def config_source(path: str | None = None) -> str:
    chosen = path or os.environ.get(_ENV_VAR)
    if not chosen:
        raise RuntimeError(
            f"No host config specified. Run `python -m core --config <path>` "
            f"or set {_ENV_VAR}."
        )
    return chosen


def _check_secret(cfg: HostConfig) -> None:
    if cfg.auth_tokens:
        sk = cfg.secret_key
        if not sk or sk == _DEFAULT_SECRET or len(sk) < _MIN_SECRET_LEN:
            raise RuntimeError(
                "auth_tokens are set but secret_key is missing, default, or too "
                "short — the session cookie would be forgeable. Set a strong "
                "secret_key (>= 16 chars), e.g.\n"
                '  python -c "import secrets; print(secrets.token_urlsafe(32))"'
            )


def load_config(path: str | None = None) -> HostConfig:
    fp = Path(config_source(path))
    if not fp.exists():
        raise FileNotFoundError(f"Host config not found: {fp}")
    with open(fp, "rb") as f:
        data = tomllib.load(f)
    cfg = HostConfig(**data)
    _check_secret(cfg)
    return cfg
