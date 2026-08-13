"""Tests for the platform-native TOML profile loader (issue #124).

All tests use tmp_path and monkeypatch — no test touches the real user home.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from hmc_mcp.config import (
    ConfigError,
    HMCConfig,
    list_profiles,
    load_profile,
    resolve_config_path,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_TOML = """\
[profiles.dev]
host = "hmc-dev.example.com"
user = "admin"
password = "devpass"
"""

TWO_PROFILE_TOML = """\
default_profile = "prod"

[profiles.prod]
host = "hmc.example.com"
user = "admin"
password = "prodpass"

[profiles.dev]
host = "hmc-dev.example.com"
user = "devadmin"
password = "devpass"
"""


def _write_toml(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# resolve_config_path — platform path derivation
# ---------------------------------------------------------------------------


def test_resolve_linux_xdg(tmp_path, monkeypatch):
    """XDG_CONFIG_HOME set → uses it."""
    xdg = tmp_path / "xdg"
    cfg = xdg / "hmc-mcp" / "config.toml"
    _write_toml(cfg, MINIMAL_TOML)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    with patch.object(sys, "platform", "linux"):
        result = resolve_config_path()
    assert result == cfg


def test_resolve_linux_fallback(tmp_path, monkeypatch):
    """XDG_CONFIG_HOME unset on Linux → ~/.config/hmc-mcp/config.toml."""
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    fake_home = tmp_path / "home"
    cfg = fake_home / ".config" / "hmc-mcp" / "config.toml"
    _write_toml(cfg, MINIMAL_TOML)
    with patch.object(sys, "platform", "linux"), \
         patch("pathlib.Path.home", return_value=fake_home):
        result = resolve_config_path()
    assert result == cfg


def test_resolve_macos(tmp_path, monkeypatch):
    """sys.platform=darwin → ~/Library/Application Support/hmc-mcp/config.toml."""
    fake_home = tmp_path / "home"
    cfg = fake_home / "Library" / "Application Support" / "hmc-mcp" / "config.toml"
    _write_toml(cfg, MINIMAL_TOML)
    with patch.object(sys, "platform", "darwin"), \
         patch("pathlib.Path.home", return_value=fake_home):
        result = resolve_config_path()
    assert result == cfg


def test_resolve_windows(tmp_path, monkeypatch):
    """sys.platform=win32, APPDATA set → %APPDATA%/hmc-mcp/config.toml."""
    appdata = tmp_path / "appdata"
    cfg = appdata / "hmc-mcp" / "config.toml"
    _write_toml(cfg, MINIMAL_TOML)
    monkeypatch.setenv("APPDATA", str(appdata))
    with patch.object(sys, "platform", "win32"):
        result = resolve_config_path()
    assert result == cfg


def test_resolve_returns_none_when_absent(tmp_path, monkeypatch):
    """Returns None when file does not exist."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))
    with patch.object(sys, "platform", "linux"):
        result = resolve_config_path()
    assert result is None


# ---------------------------------------------------------------------------
# load_profile — selection and precedence
# ---------------------------------------------------------------------------


def test_load_profile_explicit(tmp_path, monkeypatch):
    """Explicit profile arg selects the correct profile."""
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    monkeypatch.delenv("HMC_HOST", raising=False)
    monkeypatch.delenv("HMC_USER", raising=False)
    monkeypatch.delenv("HMC_PASSWORD", raising=False)
    cfg = _write_toml(tmp_path / "config.toml", TWO_PROFILE_TOML)
    result = load_profile("dev", config_path=cfg)
    assert result.host == "hmc-dev.example.com"
    assert result.user == "devadmin"
    assert result.password == "devpass"


def test_load_profile_env_var(tmp_path, monkeypatch):
    """HMC_PROFILE env var selects profile when no explicit arg given."""
    monkeypatch.setenv("HMC_PROFILE", "dev")
    monkeypatch.delenv("HMC_HOST", raising=False)
    monkeypatch.delenv("HMC_USER", raising=False)
    monkeypatch.delenv("HMC_PASSWORD", raising=False)
    cfg = _write_toml(tmp_path / "config.toml", TWO_PROFILE_TOML)
    result = load_profile(config_path=cfg)
    assert result.host == "hmc-dev.example.com"


def test_load_profile_default(tmp_path, monkeypatch):
    """default_profile in TOML used when no arg and no HMC_PROFILE."""
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    monkeypatch.delenv("HMC_HOST", raising=False)
    monkeypatch.delenv("HMC_USER", raising=False)
    monkeypatch.delenv("HMC_PASSWORD", raising=False)
    cfg = _write_toml(tmp_path / "config.toml", TWO_PROFILE_TOML)
    result = load_profile(config_path=cfg)
    assert result.host == "hmc.example.com"  # prod is default


def test_load_profile_env_overrides_toml(tmp_path, monkeypatch):
    """HMC_HOST env var beats TOML host."""
    monkeypatch.setenv("HMC_HOST", "override-host.example.com")
    monkeypatch.delenv("HMC_USER", raising=False)
    monkeypatch.delenv("HMC_PASSWORD", raising=False)
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    cfg = _write_toml(tmp_path / "config.toml", TWO_PROFILE_TOML)
    result = load_profile("prod", config_path=cfg)
    assert result.host == "override-host.example.com"


def test_load_profile_password_env(tmp_path, monkeypatch):
    """password_env is resolved from the environment."""
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    monkeypatch.delenv("HMC_HOST", raising=False)
    monkeypatch.delenv("HMC_USER", raising=False)
    monkeypatch.delenv("HMC_PASSWORD", raising=False)
    monkeypatch.setenv("MY_PW", "supersecret")
    toml = """\
[profiles.prod]
host = "hmc.example.com"
user = "admin"
password_env = "MY_PW"
"""
    cfg = _write_toml(tmp_path / "config.toml", toml)
    result = load_profile("prod", config_path=cfg)
    assert result.password == "supersecret"


def test_load_profile_both_passwords_error(tmp_path, monkeypatch):
    """Both password and password_env in TOML → ConfigError."""
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    monkeypatch.setenv("MY_PW", "x")
    toml = """\
[profiles.bad]
host = "hmc.example.com"
user = "admin"
password = "plain"
password_env = "MY_PW"
"""
    cfg = _write_toml(tmp_path / "config.toml", toml)
    with pytest.raises(ConfigError, match="password or password_env"):
        load_profile("bad", config_path=cfg)


def test_load_profile_missing_password_env(tmp_path, monkeypatch):
    """password_env references missing env var → ConfigError (no secret in message)."""
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    monkeypatch.delenv("MISSING_VAR", raising=False)
    toml = """\
[profiles.prod]
host = "hmc.example.com"
user = "admin"
password_env = "MISSING_VAR"
"""
    cfg = _write_toml(tmp_path / "config.toml", toml)
    with pytest.raises(ConfigError, match="MISSING_VAR") as exc_info:
        load_profile("prod", config_path=cfg)
    # Variable name is in the message, but no secret value should be
    assert "MISSING_VAR" in str(exc_info.value)


def test_load_profile_no_default_no_arg(tmp_path, monkeypatch):
    """No selection path → ConfigError."""
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    toml = """\
[profiles.dev]
host = "h"
user = "u"
password = "p"
"""
    cfg = _write_toml(tmp_path / "config.toml", toml)
    with pytest.raises(ConfigError, match="no default_profile"):
        load_profile(config_path=cfg)


def test_load_profile_unknown_profile(tmp_path, monkeypatch):
    """Named profile not in TOML → ConfigError listing available profiles."""
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    cfg = _write_toml(tmp_path / "config.toml", MINIMAL_TOML)
    with pytest.raises(ConfigError, match="not found"):
        load_profile("nonexistent", config_path=cfg)


def test_load_profile_toml_parse_error(tmp_path, monkeypatch):
    """Bad TOML → ConfigError with path."""
    cfg = tmp_path / "config.toml"
    cfg.write_text("this is [not valid toml ][[[", encoding="utf-8")
    with pytest.raises(ConfigError, match="TOML parse error"):
        load_profile("any", config_path=cfg)


# ---------------------------------------------------------------------------
# list_profiles
# ---------------------------------------------------------------------------


def test_list_profiles_normal(tmp_path, monkeypatch):
    """Returns profile names from config.toml."""
    cfg = _write_toml(tmp_path / "config.toml", TWO_PROFILE_TOML)
    result = list_profiles(config_path=cfg)
    assert set(result) == {"prod", "dev"}


def test_list_profiles_absent(tmp_path):
    """File absent → empty list."""
    result = list_profiles(config_path=tmp_path / "nonexistent.toml")
    assert result == []


# ---------------------------------------------------------------------------
# HMCConfig — no .env loading
# ---------------------------------------------------------------------------


def test_hmcconfig_no_env_file():
    """HMCConfig model_config must not load a .env file by default."""
    cfg_dict = HMCConfig.model_config
    # env_file should be absent, None, or an empty value — never ".env"
    env_file = cfg_dict.get("env_file")
    assert env_file != ".env", (
        "HMCConfig.model_config still has env_file='.env'; "
        "the TOML loader requires this to be removed."
    )


def test_direct_construction_still_works(monkeypatch):
    """HMCConfig(host=...) direct construction is still supported."""
    monkeypatch.delenv("HMC_HOST", raising=False)
    monkeypatch.delenv("HMC_USER", raising=False)
    monkeypatch.delenv("HMC_PASSWORD", raising=False)
    cfg = HMCConfig(host="myhost", user="myuser", password="mypass", _env_file=None)
    assert cfg.host == "myhost"
    assert cfg.user == "myuser"
    assert cfg.password == "mypass"
