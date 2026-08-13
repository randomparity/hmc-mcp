"""Tests for hmc_list_configured_hosts (issue #128).

All tests use tmp_path and monkeypatch — no test touches the real user home
or the real platform-native config file.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from hmc_mcp.server_system import hmc_list_configured_hosts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_toml(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _patch_config_path(tmp_path, content: str | None):
    """Return a context manager: patches resolve_config_path in server_system."""
    if content is None:
        return patch("hmc_mcp.server_system.resolve_config_path", return_value=None)
    cfg = _write_toml(tmp_path / "config.toml", content)
    return patch("hmc_mcp.server_system.resolve_config_path", return_value=cfg)


# ---------------------------------------------------------------------------
# Test 1: No config file
# ---------------------------------------------------------------------------

def test_no_config_file(tmp_path):
    """Returns empty profiles list when no config file exists."""
    with patch("hmc_mcp.server_system.resolve_config_path", return_value=None):
        result = hmc_list_configured_hosts()
    assert result == {"profiles": [], "config_file": None}


# ---------------------------------------------------------------------------
# Test 2: Single profile, is default
# ---------------------------------------------------------------------------

SINGLE_PROFILE_TOML = """\
default_profile = "prod"

[profiles.prod]
host = "hmc.example.com"
user = "admin"
password = "secret"  # pragma: allowlist secret
"""


def test_single_profile_is_default(tmp_path):
    """Returns correct fields; is_default=True for the default profile."""
    with _patch_config_path(tmp_path, SINGLE_PROFILE_TOML):
        result = hmc_list_configured_hosts()

    assert result["config_file"] is not None
    assert len(result["profiles"]) == 1
    p = result["profiles"][0]
    assert p["name"] == "prod"
    assert p["host"] == "hmc.example.com"
    assert p["user"] == "admin"
    assert p["is_default"] is True
    assert p["port"] == 12443       # HMCConfig default
    assert p["verify_ssl"] is False  # HMCConfig default


# ---------------------------------------------------------------------------
# Test 3: Two profiles, one default
# ---------------------------------------------------------------------------

TWO_PROFILE_TOML = """\
default_profile = "prod"

[profiles.prod]
host = "hmc.example.com"
user = "admin"
password = "prodpass"  # pragma: allowlist secret

[profiles.dev]
host = "hmc-dev.example.com"
user = "devadmin"
password = "devpass"  # pragma: allowlist secret
"""


def test_two_profiles_one_default(tmp_path):
    """Both profiles returned; only the default has is_default=True."""
    with _patch_config_path(tmp_path, TWO_PROFILE_TOML):
        result = hmc_list_configured_hosts()

    assert len(result["profiles"]) == 2
    by_name = {p["name"]: p for p in result["profiles"]}
    assert by_name["prod"]["is_default"] is True
    assert by_name["dev"]["is_default"] is False


# ---------------------------------------------------------------------------
# Test 4: Password literal present — no password value in output
# ---------------------------------------------------------------------------

PASSWORD_TOML = """\
[profiles.prod]
host = "hmc.example.com"
user = "admin"
password = "supersecret"  # pragma: allowlist secret
"""


def test_password_literal_has_password_true_no_value(tmp_path):
    """has_password=True; the literal password value must not appear in output."""
    with _patch_config_path(tmp_path, PASSWORD_TOML):
        result = hmc_list_configured_hosts()

    p = result["profiles"][0]
    assert p["has_password"] is True
    # The raw profile dict must never be forwarded; verify no password key leaks
    assert "password" not in p
    assert "supersecret" not in str(result)  # paranoid check  # pragma: allowlist secret


# ---------------------------------------------------------------------------
# Test 5: password_env present — env var NOT resolved
# ---------------------------------------------------------------------------

PASSWORD_ENV_TOML = """\
[profiles.prod]
host = "hmc.example.com"
user = "admin"
password_env = "MY_PROD_PW"  # pragma: allowlist secret
"""


def test_password_env_has_password_true_not_resolved(tmp_path, monkeypatch):
    """has_password=True when password_env key present; env var is never read."""
    monkeypatch.delenv("MY_PROD_PW", raising=False)
    with _patch_config_path(tmp_path, PASSWORD_ENV_TOML):
        result = hmc_list_configured_hosts()

    p = result["profiles"][0]
    assert p["has_password"] is True
    assert "password_env" not in p


# ---------------------------------------------------------------------------
# Test 6: No credentials — has_password=False, has_ssh_key=False
# ---------------------------------------------------------------------------

NO_CRED_TOML = """\
[profiles.test]
host = "hmc.example.com"
user = "admin"
"""


def test_no_credentials_false_booleans(tmp_path):
    """has_password=False and has_ssh_key=False when no credential keys present."""
    with _patch_config_path(tmp_path, NO_CRED_TOML):
        result = hmc_list_configured_hosts()

    p = result["profiles"][0]
    assert p["has_password"] is False
    assert p["has_ssh_key"] is False


# ---------------------------------------------------------------------------
# Test 7: ssh_key_file present — has_ssh_key=True, no key content
# ---------------------------------------------------------------------------

SSH_KEY_TOML = """\
[profiles.prod]
host = "hmc.example.com"
user = "admin"
ssh_key_file = "/home/user/.ssh/id_rsa"
"""


def test_ssh_key_has_ssh_key_true_no_content(tmp_path):
    """has_ssh_key=True; key path and content must not appear in output."""
    with _patch_config_path(tmp_path, SSH_KEY_TOML):
        result = hmc_list_configured_hosts()

    p = result["profiles"][0]
    assert p["has_ssh_key"] is True
    assert "ssh_key_file" not in p
    assert "/home/user/.ssh" not in str(result)


# ---------------------------------------------------------------------------
# Test 8: TOML parse error → ValueError with config path
# ---------------------------------------------------------------------------

def test_toml_parse_error(tmp_path):
    """TOML parse error → ValueError whose message includes the config path."""
    cfg = tmp_path / "config.toml"
    cfg.write_text("this is [[not valid toml]]\n", encoding="utf-8")
    with patch("hmc_mcp.server_system.resolve_config_path", return_value=cfg):
        with pytest.raises(ValueError, match="TOML parse error"):
            hmc_list_configured_hosts()


# ---------------------------------------------------------------------------
# Test 9: PermissionError reading config → ValueError with path and OS error
# ---------------------------------------------------------------------------

def test_permission_error_reading_config(tmp_path):
    """PermissionError reading config file → ValueError with path and OS error."""
    cfg = tmp_path / "config.toml"
    cfg.write_text("[profiles.x]\nhost = 'h'\nuser = 'u'\n", encoding="utf-8")
    with patch("hmc_mcp.server_system.resolve_config_path", return_value=cfg), \
         patch.object(Path, "read_text", side_effect=PermissionError("Permission denied")):
        with pytest.raises(ValueError, match="cannot read config file"):
            hmc_list_configured_hosts()


# ---------------------------------------------------------------------------
# Test 10a/10b: port and verify_ssl — explicit values and HMCConfig defaults
# ---------------------------------------------------------------------------

CUSTOM_PORT_TOML = """\
[profiles.prod]
host = "hmc.example.com"
user = "admin"
password = "p"  # pragma: allowlist secret
port = 9999
verify_ssl = true
"""

DEFAULTS_TOML = """\
[profiles.prod]
host = "hmc.example.com"
user = "admin"
password = "p"  # pragma: allowlist secret
"""


def test_port_verify_ssl_explicit_values(tmp_path):
    """Explicit port and verify_ssl in TOML are used."""
    with _patch_config_path(tmp_path, CUSTOM_PORT_TOML):
        result = hmc_list_configured_hosts()
    p = result["profiles"][0]
    assert p["port"] == 9999
    assert p["verify_ssl"] is True


def test_port_verify_ssl_defaults_from_hmcconfig(tmp_path):
    """When port and verify_ssl are absent, defaults come from HMCConfig.model_fields."""
    from hmc_mcp.config import HMCConfig
    expected_port = int(HMCConfig.model_fields["port"].default)
    expected_verify_ssl = bool(HMCConfig.model_fields["verify_ssl"].default)

    with _patch_config_path(tmp_path, DEFAULTS_TOML):
        result = hmc_list_configured_hosts()

    p = result["profiles"][0]
    assert p["port"] == expected_port
    assert p["verify_ssl"] == expected_verify_ssl
