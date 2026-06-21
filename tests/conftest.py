"""Test fixtures. Points the host config at a temp TOML before importing the app,
and offers a make_app factory that builds an app with host-config overrides.
"""

import os
import tempfile

_fd, _CFG = tempfile.mkstemp(suffix=".toml")
with os.fdopen(_fd, "w") as f:
    f.write(
        'host = "127.0.0.1"\n'
        "port = 9090\n"
        'secret_key = "test-secret-key-0123456789abcdef"\n'
        "auth_tokens = []\n"
    )
os.environ["MARKET_UTILS_CONFIG"] = _CFG

import pytest  # noqa: E402

from core import config as host_config  # noqa: E402
from core import main  # noqa: E402
from core.registry import discover_modules  # noqa: E402


@pytest.fixture
def make_app():
    base = host_config.load_config()
    modules = discover_modules()

    def _make(**overrides):
        cfg = base.model_copy(update=overrides)
        return main.build_app(cfg, modules)

    return _make
