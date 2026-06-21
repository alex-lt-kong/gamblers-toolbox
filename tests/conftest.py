"""Test fixtures. Points the host config at a temp TOML before core.main is
imported (it loads host config at import today), and offers a make_app factory
that builds an app with host-config overrides.
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

import core.main as main  # noqa: E402


@pytest.fixture
def make_app():
    base = dict(main.CONFIG)

    def _make(**overrides):
        main.CONFIG.clear()
        main.CONFIG.update(base)
        main.CONFIG.update(overrides)
        return main.build_app()

    yield _make
    main.CONFIG.clear()
    main.CONFIG.update(base)
