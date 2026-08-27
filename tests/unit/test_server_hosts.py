"""Tests for hmc_list_configured_hosts (issue #128).

All tests use tmp_path and monkeypatch — no test touches the real user home
or the real platform-native config file.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from hmc_mcp.server_tools.systems import hmc_list_configured_hosts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_toml(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _patch_config_path(tmp_path, content: str | None):
    """Return a context manager patching the systems handler config lookup."""
    if content is None:
        return patch("hmc_mcp.server_tools.systems.resolve_config_path", return_value=None)
    cfg = _write_toml(tmp_path / "config.toml", content)
    return patch("hmc_mcp.server_tools.systems.resolve_config_path", return_value=cfg)


# ---------------------------------------------------------------------------
# Test 1: No config file
# ---------------------------------------------------------------------------

def test_no_config_file(tmp_path):
    """Returns empty profiles list when no config file exists."""
    with patch("hmc_mcp.server_tools.systems.resolve_config_path", return_value=None):
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
    assert p["port"] == 443         # HMCConfig default
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
    with patch("hmc_mcp.server_tools.systems.resolve_config_path", return_value=cfg):
        with pytest.raises(ValueError, match="TOML parse error"):
            hmc_list_configured_hosts()


# ---------------------------------------------------------------------------
# Test 9: PermissionError reading config → ValueError with path and OS error
# ---------------------------------------------------------------------------

def test_permission_error_reading_config(tmp_path):
    """PermissionError reading config file → ValueError with path and OS error."""
    cfg = tmp_path / "config.toml"
    cfg.write_text("[profiles.x]\nhost = 'h'\nuser = 'u'\n", encoding="utf-8")
    with patch("hmc_mcp.server_tools.systems.resolve_config_path", return_value=cfg), \
         patch.object(Path, "read_text", side_effect=PermissionError("Permission denied")):
        with pytest.raises(ValueError, match="cannot be read"):
            hmc_list_configured_hosts()


def test_non_utf8_config(tmp_path):
    """A non-UTF-8 config file → ValueError, not a UnicodeDecodeError (#257)."""
    cfg = tmp_path / "config.toml"
    cfg.write_bytes(b'[profiles.x]\nhost = "caf\xe9"\n')
    with patch("hmc_mcp.server_tools.systems.resolve_config_path", return_value=cfg):
        with pytest.raises(ValueError, match="is not valid UTF-8"):
            hmc_list_configured_hosts()


def test_non_table_profiles_key(tmp_path):
    """`profiles = "x"` → ValueError, not an AttributeError on .items() (#257)."""
    cfg = tmp_path / "config.toml"
    cfg.write_text("profiles = 'not-a-table'\n", encoding="utf-8")
    with patch("hmc_mcp.server_tools.systems.resolve_config_path", return_value=cfg):
        with pytest.raises(ValueError, match="'profiles' must be a table"):
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


# ---------------------------------------------------------------------------
# Nickname surfacing (issue #226)
# ---------------------------------------------------------------------------

NICKNAME_TOML = """\
default_profile = "prod"

[profiles.prod]
host = "prod-hmc.example.com"
user = "admin"
password_env = "HMC_PROD_PW"     # pragma: allowlist secret

[profiles.stg]
host = "stg-hmc.example.com"
user = "admin"
password = "stgpass"     # pragma: allowlist secret

[nicknames]
big-iron = "prod"
staging = "stg"
ghost = "does-not-exist"
"""


def test_list_configured_hosts_surfaces_nicknames(tmp_path):
    """hmc_list_configured_hosts reports each nickname + target-existence."""
    with _patch_config_path(tmp_path, NICKNAME_TOML):
        result = hmc_list_configured_hosts()

    by_name = {n["name"]: n for n in result["nicknames"]}
    assert by_name["big-iron"]["target"] == "prod"
    assert by_name["big-iron"]["target_exists"] is True
    assert by_name["staging"]["target_exists"] is True
    assert by_name["ghost"]["target_exists"] is False


def test_list_configured_hosts_nicknames_secret_free(tmp_path, monkeypatch):
    """Nicknames are surfaced without resolving or leaking any credential."""
    monkeypatch.delenv("HMC_PROD_PW", raising=False)
    with _patch_config_path(tmp_path, NICKNAME_TOML):
        result = hmc_list_configured_hosts()

    rendered = str(result)
    assert "HMC_PROD_PW" not in rendered
    assert "stgpass" not in rendered   # pragma: allowlist secret
    assert "nicknames" in result


# ---------------------------------------------------------------------------
# Malformed nicknames table -> ValueError (issue #226, finding #2)
#
# A malformed nicknames table must surface as an error, not collapse into an
# empty nickname inventory that would hide the broken config while
# nickname-based connections silently fail.
# ---------------------------------------------------------------------------

NICKNAMES_NOT_TABLE_TOML = """\
default_profile = "prod"
nicknames = ["big-iron", "staging"]

[profiles.prod]
host = "prod-hmc.example.com"
user = "admin"
"""


def test_malformed_nicknames_not_table_raises(tmp_path):
    """A non-table nicknames value raises, not an empty nickname inventory."""
    with _patch_config_path(tmp_path, NICKNAMES_NOT_TABLE_TOML):
        with pytest.raises(ValueError, match="must be a table"):
            hmc_list_configured_hosts()


NICKNAMES_NON_STRING_TARGET_TOML = """\
default_profile = "prod"

[profiles.prod]
host = "prod-hmc.example.com"
user = "admin"

[nicknames]
big-iron = 42
"""


def test_malformed_nicknames_non_string_target_raises(tmp_path):
    """A non-string nickname target raises, not an empty nickname inventory."""
    with _patch_config_path(tmp_path, NICKNAMES_NON_STRING_TARGET_TOML):
        with pytest.raises(ValueError, match="must map to a profile-key string"):
            hmc_list_configured_hosts()


# ---------------------------------------------------------------------------
# Read count (issue #295) — one parse of config.toml per invocation
# ---------------------------------------------------------------------------

READ_COUNT_TOML = """\
default_profile = "prod"

[profiles.prod]
host = "hmc.example.com"
user = "admin"
password = "prodpass"  # pragma: allowlist secret

[nicknames]
big-iron = "prod"
"""


def test_reads_config_document_exactly_once(tmp_path):
    """hmc_list_configured_hosts parses config.toml once, not twice (#295).

    Patches the shared choke point `_read_config_document` in both the module
    that owns it (`hmc_mcp.config`, where `list_nicknames` resolves the name as
    a module global at call time) and `hmc_mcp.server_tools.systems`'s own imported
    name (its direct call site), so every read reaches the same counter
    regardless of which call site makes it.
    """
    from unittest.mock import MagicMock

    import hmc_mcp.config as config_mod
    import hmc_mcp.server_tools.systems as server_systems_mod

    cfg = _write_toml(tmp_path / "config.toml", READ_COUNT_TOML)
    counter = MagicMock(wraps=config_mod._read_config_document)
    with (
        patch.object(config_mod, "_read_config_document", counter),
        patch.object(server_systems_mod, "_read_config_document", counter),
        patch.object(server_systems_mod, "resolve_config_path", return_value=cfg),
    ):
        result = hmc_list_configured_hosts()

    assert result["profiles"][0]["name"] == "prod"
    assert counter.call_count == 1
