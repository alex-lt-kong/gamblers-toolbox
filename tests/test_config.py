import pytest

from core import config


def _write(tmp_path, body):
    p = tmp_path / "c.toml"
    p.write_text(body)
    return str(p)


def test_load_fills_defaults(tmp_path):
    cfg = config.load_config(_write(tmp_path, "auth_tokens = []\nport = 1234\n"))
    assert cfg["port"] == 1234
    assert cfg["host"] == "127.0.0.1"  # default filled in


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        config.load_config(str(tmp_path / "nope.toml"))


def test_unspecified_raises(monkeypatch):
    monkeypatch.delenv("MARKET_UTILS_CONFIG", raising=False)
    with pytest.raises(RuntimeError):
        config.load_config()


def test_default_secret_with_auth_rejected(tmp_path):
    body = 'auth_tokens = ["x"]\nsecret_key = "dev-insecure-change-me"\n'
    with pytest.raises(RuntimeError):
        config.load_config(_write(tmp_path, body))


def test_short_secret_with_auth_rejected(tmp_path):
    with pytest.raises(RuntimeError):
        config.load_config(_write(tmp_path, 'auth_tokens = ["x"]\nsecret_key = "short"\n'))


def test_default_secret_ok_when_auth_off(tmp_path):
    body = 'auth_tokens = []\nsecret_key = "dev-insecure-change-me"\n'
    assert config.load_config(_write(tmp_path, body))["auth_tokens"] == []


def test_module_config_missing_gives_clear_error():
    from modules.pe_monitor import config as pcfg
    with pytest.raises(FileNotFoundError):
        pcfg.load_config("definitely-missing.toml")
