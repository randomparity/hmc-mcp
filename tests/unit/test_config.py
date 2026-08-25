"""Tests for the platform-native TOML profile loader (issue #124).

All tests use tmp_path and monkeypatch — no test touches the real user home.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

import pytest

from hmc_mcp.config import (
    ConfigError,
    HMCConfig,
    config_dir,
    list_nicknames,
    list_profiles,
    list_profiles_and_nicknames,
    list_profiles_with_default,
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
password = "devpass"  # pragma: allowlist secret
"""

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


def test_omitted_port_defaults_to_443_without_explicit_provenance(monkeypatch):
    monkeypatch.delenv("HMC_PORT", raising=False)

    config = HMCConfig(_env_file=None)

    assert config.port == 443
    assert "port" not in config.model_fields_set


def test_constructor_port_is_explicit_even_when_it_matches_default(monkeypatch):
    monkeypatch.delenv("HMC_PORT", raising=False)

    config = HMCConfig(port=443, _env_file=None)

    assert config.port == 443
    assert "port" in config.model_fields_set


def test_environment_port_is_explicit(monkeypatch):
    monkeypatch.setenv("HMC_PORT", "12443")

    config = HMCConfig(_env_file=None)

    assert config.port == 12443
    assert "port" in config.model_fields_set


def test_toml_port_is_explicit(tmp_path, monkeypatch):
    monkeypatch.delenv("HMC_PORT", raising=False)
    cfg = _write_toml(
        tmp_path / "config.toml",
        MINIMAL_TOML + "port = 12443\n",
    )

    config = load_profile("dev", config_path=cfg)

    assert config.port == 12443
    assert "port" in config.model_fields_set


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
    assert result.password == "devpass"  # pragma: allowlist secret


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
password_env = "MY_PW"  # pragma: allowlist secret
"""
    cfg = _write_toml(tmp_path / "config.toml", toml)
    result = load_profile("prod", config_path=cfg)
    assert result.password == "supersecret"  # pragma: allowlist secret


def test_load_profile_both_passwords_error(tmp_path, monkeypatch):
    """Both password and password_env in TOML → ConfigError."""
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    monkeypatch.setenv("MY_PW", "x")
    toml = """\
[profiles.bad]
host = "hmc.example.com"
user = "admin"
password = "plain"  # pragma: allowlist secret
password_env = "MY_PW"  # pragma: allowlist secret
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
password_env = "MISSING_VAR"  # pragma: allowlist secret
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
    cfg = HMCConfig(host="myhost", user="myuser", password="mypass", _env_file=None)  # pragma: allowlist secret
    assert cfg.host == "myhost"
    assert cfg.user == "myuser"
    assert cfg.password == "mypass"  # pragma: allowlist secret


# ---------------------------------------------------------------------------
# config_dir — unconditional platform path (no existence check)
# ---------------------------------------------------------------------------


def test_config_dir_linux_xdg(monkeypatch):
    """config_dir() returns XDG-based path without checking existence."""
    xdg = Path("/tmp/fake_xdg")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    with patch.object(sys, "platform", "linux"):
        result = config_dir()
    assert result == xdg / "hmc-mcp"


def test_config_dir_macos(monkeypatch):
    """config_dir() returns ~/Library/Application Support/hmc-mcp on macOS."""
    fake_home = Path("/tmp/fake_home")
    with patch.object(sys, "platform", "darwin"), \
         patch("pathlib.Path.home", return_value=fake_home):
        result = config_dir()
    assert result == fake_home / "Library" / "Application Support" / "hmc-mcp"


def test_config_dir_returns_path_even_when_absent(tmp_path, monkeypatch):
    """config_dir() does not require the directory to exist."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "nonexistent"))
    with patch.object(sys, "platform", "linux"):
        result = config_dir()
    assert not result.exists()
    assert result.name == "hmc-mcp"


# ---------------------------------------------------------------------------
# list_profiles_with_default
# ---------------------------------------------------------------------------


def test_list_profiles_with_default_normal(tmp_path, monkeypatch):
    """Returns (names, default) from TOML."""
    cfg = _write_toml(tmp_path / "config.toml", TWO_PROFILE_TOML)
    names, default = list_profiles_with_default(config_path=cfg)
    assert set(names) == {"prod", "dev"}
    assert default == "prod"


def test_list_profiles_with_default_no_default(tmp_path, monkeypatch):
    """Returns (names, None) when no default_profile key."""
    cfg = _write_toml(tmp_path / "config.toml", MINIMAL_TOML)
    names, default = list_profiles_with_default(config_path=cfg)
    assert "dev" in names
    assert default is None


def test_list_profiles_with_default_absent(tmp_path):
    """Returns ([], None) when file absent."""
    names, default = list_profiles_with_default(config_path=tmp_path / "nonexistent.toml")
    assert names == []
    assert default is None


# ---------------------------------------------------------------------------
# HMC_AGENT_ID and effective_audit_memento (issue #132)
# ---------------------------------------------------------------------------


def test_agent_id_unset_uses_audit_memento_default():
    cfg = HMCConfig(_env_file=None)
    assert cfg.agent_id is None
    assert cfg.effective_audit_memento == "hmc-mcp"


def test_agent_id_set_prefixes_audit_memento():
    cfg = HMCConfig(agent_id="alice", _env_file=None)
    assert cfg.effective_audit_memento == "hmc-mcp:alice"


def test_agent_id_overrides_audit_memento_field():
    # When agent_id is set, effective_audit_memento uses hmc-mcp:<agent_id>
    # regardless of the audit_memento field.
    # Setting both also emits a UserWarning at construction time.
    with pytest.warns(UserWarning, match="HMC_AGENT_ID is set"):
        cfg = HMCConfig(agent_id="bob", audit_memento="custom", _env_file=None)
    assert cfg.effective_audit_memento == "hmc-mcp:bob"


def test_agent_id_no_warning_when_audit_memento_is_default():
    # When audit_memento is default ('hmc-mcp'), no warning is emitted even
    # when agent_id is set, because there is no custom value being silently discarded.
    import warnings as _warnings
    with _warnings.catch_warnings():
        _warnings.simplefilter("error", UserWarning)
        cfg = HMCConfig(agent_id="alice", _env_file=None)
    assert cfg.effective_audit_memento == "hmc-mcp:alice"


def test_audit_memento_without_agent_id():
    cfg = HMCConfig(audit_memento="my-tool", _env_file=None)
    assert cfg.effective_audit_memento == "my-tool"


def test_agent_id_invalid_raises_at_construction():
    with pytest.raises(ValueError, match="comma"):
        HMCConfig(agent_id="bad,id", _env_file=None)


def test_agent_id_from_env(monkeypatch):
    monkeypatch.setenv("HMC_AGENT_ID", "env-agent")
    cfg = HMCConfig(_env_file=None)
    assert cfg.agent_id == "env-agent"
    assert cfg.effective_audit_memento == "hmc-mcp:env-agent"


# ---------------------------------------------------------------------------
# Nickname resolution (issue #226)
# ---------------------------------------------------------------------------

NICKNAME_TOML = """\
default_profile = "prod"

[profiles.prod]
host = "prod-hmc.example.com"
user = "admin"
password = "prodpass"    # pragma: allowlist secret

[profiles.stg]
host = "stg-hmc.example.com"
user = "admin"
password = "stgpass"    # pragma: allowlist secret

[nicknames]
big-iron = "prod"
staging = "stg"
"""


def test_nickname_resolves_via_explicit_profile_arg(tmp_path, monkeypatch):
    """A --profile-style explicit arg that is a nickname resolves to its target."""
    cfg = _write_toml(tmp_path / "config.toml", NICKNAME_TOML)
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    result = load_profile(profile="big-iron", config_path=cfg)
    assert result.host == "prod-hmc.example.com"


def test_nickname_resolves_via_hmc_profile_env(tmp_path, monkeypatch):
    """HMC_PROFILE carrying a nickname resolves to its target profile."""
    cfg = _write_toml(tmp_path / "config.toml", NICKNAME_TOML)
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    monkeypatch.setenv("HMC_PROFILE", "staging")
    result = load_profile(profile=None, config_path=cfg)
    assert result.host == "stg-hmc.example.com"


def test_nickname_resolves_via_default_profile(tmp_path, monkeypatch):
    """A default_profile that is a nickname resolves to its target profile."""
    toml = NICKNAME_TOML.replace(
        'default_profile = "prod"', 'default_profile = "big-iron"'
    )
    cfg = _write_toml(tmp_path / "config.toml", toml)
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    result = load_profile(profile=None, config_path=cfg)
    assert result.host == "prod-hmc.example.com"


def test_profile_key_wins_over_nickname_collision(tmp_path, monkeypatch):
    """A name that is both a profile key and a nickname key uses the profile."""
    toml = """\
default_profile = "prod"

[profiles.prod]
host = "real-prod.example.com"
user = "admin"
password = "p"    # pragma: allowlist secret

[profiles.dev]
host = "dev.example.com"
user = "admin"
password = "d"    # pragma: allowlist secret

[nicknames]
prod = "dev"
"""
    cfg = _write_toml(tmp_path / "config.toml", toml)
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    result = load_profile(profile="prod", config_path=cfg)
    assert result.host == "real-prod.example.com"


def test_nickname_missing_target_raises_config_error(tmp_path, monkeypatch):
    """A nickname whose target is not a profile raises ConfigError naming names."""
    toml = """\
default_profile = "prod"

[profiles.prod]
host = "h.example.com"
user = "admin"
password = "p"    # pragma: allowlist secret

[nicknames]
ghost = "does-not-exist"
"""
    cfg = _write_toml(tmp_path / "config.toml", toml)
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    with pytest.raises(ConfigError) as exc:
        load_profile(profile="ghost", config_path=cfg)
    msg = str(exc.value)
    assert "ghost" in msg
    assert "does-not-exist" in msg
    assert "prod" in msg    # available profiles named


def test_unknown_name_config_error_names_nicknames(tmp_path, monkeypatch):
    """An unknown name (not a profile, not a nickname) names profiles + nicknames."""
    cfg = _write_toml(tmp_path / "config.toml", NICKNAME_TOML)
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    with pytest.raises(ConfigError) as exc:
        load_profile(profile="nope", config_path=cfg)
    msg = str(exc.value)
    assert "big-iron" in msg    # a nickname is named
    assert "prod" in msg        # a profile is named


def test_nickname_resolution_is_case_sensitive(tmp_path, monkeypatch):
    """Nickname matching is case-sensitive: BIG-IRON does not match big-iron."""
    cfg = _write_toml(tmp_path / "config.toml", NICKNAME_TOML)
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    with pytest.raises(ConfigError):
        load_profile(profile="BIG-IRON", config_path=cfg)


def test_nickname_one_level_no_chaining(tmp_path, monkeypatch):
    """A nickname whose target is another nickname is NOT resolved (one level)."""
    toml = """\
default_profile = "prod"

[profiles.prod]
host = "h.example.com"
user = "admin"
password = "p"    # pragma: allowlist secret

[nicknames]
a = "b"
b = "prod"
"""
    cfg = _write_toml(tmp_path / "config.toml", toml)
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    # "a" -> "b", but "b" is not a profile key, so resolution fails.
    with pytest.raises(ConfigError, match="b"):
        load_profile(profile="a", config_path=cfg)


def test_malformed_nicknames_table_raises_config_error(tmp_path, monkeypatch):
    """A nicknames table with a non-string value raises ConfigError."""
    toml = """\
default_profile = "prod"

[profiles.prod]
host = "h.example.com"
user = "admin"
password = "p"    # pragma: allowlist secret

[nicknames]
big-iron = 42
"""
    cfg = _write_toml(tmp_path / "config.toml", toml)
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    with pytest.raises(ConfigError):
        load_profile(profile="big-iron", config_path=cfg)


def test_malformed_nicknames_non_table_raises(tmp_path, monkeypatch):
    """A top-level nicknames key that is not a table raises ConfigError."""
    toml = """\
default_profile = "prod"
nicknames = "not-a-table"

[profiles.prod]
host = "h.example.com"
user = "admin"
password = "p"      # pragma: allowlist secret
"""
    cfg = _write_toml(tmp_path / "config.toml", toml)
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    with pytest.raises(ConfigError):
        load_profile(profile="big-iron", config_path=cfg)


def test_malformed_nicknames_table_fails_regardless_of_profile(tmp_path, monkeypatch):
    """A malformed nicknames table raises even when a valid profile is selected.

    Per ADR 0030, load_profile validates the nicknames structure whenever the
    key is present, not only when a nickname is resolved.
    """
    toml = """\
default_profile = "prod"

[profiles.prod]
host = "prod-hmc.example.com"
user = "admin"
password = "prodpass"     # pragma: allowlist secret

[nicknames]
big-iron = 42
"""
    cfg = _write_toml(tmp_path / "config.toml", toml)
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    with pytest.raises(ConfigError):
        load_profile(profile="prod", config_path=cfg)


def test_well_formed_nicknames_do_not_block_plain_profile(tmp_path, monkeypatch):
    """A well-formed nicknames table does not block selecting a plain profile."""
    cfg = _write_toml(tmp_path / "config.toml", NICKNAME_TOML)
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    result = load_profile(profile="prod", config_path=cfg)
    assert result.host == "prod-hmc.example.com"


def test_list_nicknames_present(tmp_path):
    """list_nicknames returns the nicknames table as dict[str, str]."""
    cfg = _write_toml(tmp_path / "config.toml", NICKNAME_TOML)
    assert list_nicknames(config_path=cfg) == {"big-iron": "prod", "staging": "stg"}


def test_list_nicknames_absent(tmp_path):
    """list_nicknames returns {} when no nicknames table or no file."""
    cfg = _write_toml(tmp_path / "config.toml", TWO_PROFILE_TOML)
    assert list_nicknames(config_path=cfg) == {}
    assert list_nicknames(config_path=tmp_path / "nonexistent.toml") == {}


# ---------------------------------------------------------------------------
# list_profiles_and_nicknames (issue #222)
# ---------------------------------------------------------------------------


def test_list_profiles_and_nicknames_returns_both_tables(tmp_path):
    """Both selection tables come back from one call, so they cannot disagree."""

    cfg = _write_toml(tmp_path / "config.toml", NICKNAME_TOML)
    profiles, nicknames = list_profiles_and_nicknames(config_path=cfg)
    assert set(profiles) == {"prod", "stg"}
    assert nicknames == {"big-iron": "prod", "staging": "stg"}


def test_list_profiles_and_nicknames_absent_file(tmp_path):
    """An absent file is an empty configuration, not an error."""

    assert list_profiles_and_nicknames(config_path=tmp_path / "nope.toml") == ([], {})


def test_list_profiles_and_nicknames_rejects_malformed_nicknames(tmp_path):
    """A malformed nicknames table raises, as it does for list_nicknames."""

    cfg = _write_toml(
        tmp_path / "config.toml",
        NICKNAME_TOML.replace('big-iron = "prod"', "big-iron = 7"),
    )
    with pytest.raises(ConfigError, match="must map to a profile-key string"):
        list_profiles_and_nicknames(config_path=cfg)


# ---------------------------------------------------------------------------
# The read-and-parse failure contract shared by every reader (issue #257)
#
# Each reader documents ConfigError as its failure type, so a
# ``try/except ConfigError`` around any of them must actually catch. These cases
# are parametrized over all five rather than written per reader: before #257
# only list_profiles_and_nicknames converted them, and the other four leaked a
# PermissionError, an IsADirectoryError, a UnicodeDecodeError, a RecursionError,
# or an AttributeError naming the absolute config path.
# ---------------------------------------------------------------------------

_READERS = {
    "list_profiles_with_default": lambda p: list_profiles_with_default(config_path=p),
    "list_profiles": lambda p: list_profiles(config_path=p),
    "list_nicknames": lambda p: list_nicknames(config_path=p),
    "list_profiles_and_nicknames": lambda p: list_profiles_and_nicknames(config_path=p),
    "load_profile": lambda p: load_profile("prod", config_path=p),
}

# Every reader that consults the ``profiles`` table. list_nicknames is absent
# because it returns the other half of the document and never touches this one.
_PROFILE_READERS = {
    name: call for name, call in _READERS.items() if name != "list_nicknames"
}


@pytest.fixture(params=list(_READERS), ids=list(_READERS))
def reader(request):
    """Each config.toml reader in turn, called with an explicit config path."""
    return _READERS[request.param]


@pytest.fixture(params=list(_PROFILE_READERS), ids=list(_PROFILE_READERS))
def profile_reader(request):
    """Each reader that consults the ``profiles`` table."""
    return _PROFILE_READERS[request.param]


@pytest.fixture
def unreadable_config(tmp_path):
    """A well-formed config.toml with no read permission, restored on teardown."""
    cfg = _write_toml(tmp_path / "config.toml", TWO_PROFILE_TOML)
    cfg.chmod(0o000)
    yield cfg
    cfg.chmod(0o600)


def test_reader_rejects_an_unreadable_file(reader, unreadable_config):
    """A PermissionError must arrive as a ConfigError, not as a raw OSError."""

    with pytest.raises(ConfigError, match="cannot be read"):
        reader(unreadable_config)


def test_reader_rejects_a_directory(reader, tmp_path):
    """A directory at the config path is an OSError on read, not an absent file."""

    directory = tmp_path / "config.toml"
    directory.mkdir()
    with pytest.raises(ConfigError, match="cannot be read"):
        reader(directory)


def test_reader_rejects_a_null_byte_in_the_path(reader, tmp_path):
    """read_text raises ValueError for an unusable path before it reaches the fs."""

    cfg = _write_toml(tmp_path / "config.toml", TWO_PROFILE_TOML)
    with pytest.raises(ConfigError, match="cannot be read"):
        reader(Path(f"{cfg}\x00suffix"))


def test_reader_rejects_non_utf8(reader, tmp_path):
    """A latin-1 config file is a ConfigError, not a UnicodeDecodeError."""

    cfg = tmp_path / "config.toml"
    cfg.write_bytes(b'[profiles.prod]\nhost = "caf\xe9"\n')
    with pytest.raises(ConfigError, match="is not valid UTF-8"):
        reader(cfg)


def test_reader_rejects_invalid_toml(reader, tmp_path):
    """A parse error is a ConfigError naming the path."""

    cfg = tmp_path / "config.toml"
    cfg.write_text("this is [not valid toml ][[[", encoding="utf-8")
    with pytest.raises(ConfigError, match="TOML parse error"):
        reader(cfg)


def test_reader_rejects_a_deeply_nested_document(reader, tmp_path):
    """tomllib recurses on nested arrays; the stack runs out before the parser does."""

    cfg = tmp_path / "config.toml"
    cfg.write_text("deep = " + "[" * 3000 + "]" * 3000, encoding="utf-8")
    with pytest.raises(ConfigError, match="document nesting is too deep"):
        reader(cfg)


def test_reader_reports_an_unresolvable_home(reader, monkeypatch):
    """Path.home() raises under a uid with no passwd entry and no HOME."""

    def _no_home():
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr("pathlib.Path.home", _no_home)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    with pytest.raises(ConfigError, match="cannot resolve the config path"):
        reader(None)


@pytest.mark.parametrize(
    ("name", "empty"),
    [
        ("list_profiles_with_default", ([], None)),
        ("list_profiles", []),
        ("list_nicknames", {}),
        ("list_profiles_and_nicknames", ([], {})),
    ],
)
def test_listing_reader_with_no_platform_config_file(name, empty, tmp_path, monkeypatch):
    """No config.toml anywhere is an empty configuration, not a failure."""

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))
    with patch.object(sys, "platform", "linux"):
        assert _READERS[name](None) == empty


def test_load_profile_with_no_platform_config_file(tmp_path, monkeypatch):
    """load_profile has nothing to select from, so it raises rather than returns."""

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    with patch.object(sys, "platform", "linux"):
        with pytest.raises(ConfigError, match="not found"):
            _READERS["load_profile"](None)


def test_profile_reader_rejects_a_non_table_profiles_key(profile_reader, tmp_path):
    """`profiles = "x"` used to reach `.keys()` on a str, or a substring test."""

    cfg = _write_toml(tmp_path / "config.toml", "profiles = 'not-a-table'\n")
    with pytest.raises(ConfigError, match="'profiles' must be a table"):
        profile_reader(cfg)


def test_load_profile_rejects_a_non_table_profile_entry(tmp_path, monkeypatch):
    """A selected profile that is not a table is a ConfigError, not dict()'s."""

    monkeypatch.delenv("HMC_PROFILE", raising=False)
    cfg = _write_toml(tmp_path / "config.toml", "[profiles]\nprod = 'not-a-table'\n")
    with pytest.raises(ConfigError, match="profile 'prod' must be a table"):
        load_profile("prod", config_path=cfg)


# ---------------------------------------------------------------------------
# Environment-isolated construction: HMCConfig.from_mapping (issue #368, ADR 0096)
# ---------------------------------------------------------------------------

#: The three vars that silently redirect a backend: a stray HMC_HOST points it at
#: the wrong HMC, a stray HMC_SSH_KEY_FILE offers the wrong private key, and a
#: stray HMC_AGENT_ID corrupts ADR 0011 ownership attribution on every LPAR the
#: process stamps.
LEAKY_ENVIRONMENT = {
    "HMC_HOST": "leaked-host.example.com",
    "HMC_AGENT_ID": "leaked-agent",
    "HMC_SSH_KEY_FILE": "/leaked/id_rsa",
}


def _non_default_env_value(field_name: str, field_info) -> str:
    """A valid, non-default environment string for *field_name*.

    Derived from the declared annotation rather than listed per field, so a new
    setting is polluted by the tests below without editing them.
    """
    annotation = field_info.annotation
    if annotation is bool:
        return "true"
    if annotation is int:
        return "9999"
    if annotation is float:
        return "9.5"
    return f"leak-{field_name}"


def test_from_mapping_ignores_ambient_environment(monkeypatch):
    """Issue #368's shape: one config per HMC built from a row, in a polluted process."""
    for name, value in LEAKY_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)

    cfg = HMCConfig.from_mapping({"host": "row-host.example.com", "user": "rowuser"})

    assert cfg.host == "row-host.example.com"
    assert cfg.agent_id is None
    assert cfg.ssh_key_file is None


def test_plain_constructor_still_reads_the_environment(monkeypatch):
    """The operator path is deliberately unchanged — from_mapping is additive.

    Pinned so that a future attempt to make ``HMCConfig()`` itself isolated
    cannot land silently; that would break the CLI and the MCP server.
    """
    for name, value in LEAKY_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)

    cfg = HMCConfig(host="row-host.example.com")

    assert cfg.host == "row-host.example.com"
    assert cfg.agent_id == "leaked-agent"
    assert cfg.ssh_key_file == "/leaked/id_rsa"


def test_from_mapping_leaves_no_field_to_the_environment(monkeypatch):
    """Every field, not just the three above, takes its declared default.

    Enumerated from ``model_fields``, so a new setting that ``from_mapping``
    fails to pass explicitly is caught here rather than leaking in production.
    """
    prefix = HMCConfig.model_config["env_prefix"]
    env_names = {
        name: f"{prefix}{name.upper()}" for name in HMCConfig.model_fields
    }
    for env_name in env_names.values():
        monkeypatch.delenv(env_name, raising=False)
    pristine = HMCConfig().model_dump()

    for field_name, env_name in env_names.items():
        monkeypatch.setenv(
            env_name,
            _non_default_env_value(field_name, HMCConfig.model_fields[field_name]),
        )

    with warnings.catch_warnings():
        # A polluted env sets agent_id and audit_memento together, which the
        # model validator warns about; that warning is this test's setup, not
        # its subject.
        warnings.simplefilter("ignore", UserWarning)
        polluted = HMCConfig().model_dump()

    # The pollution has to be visible somewhere, or the assertion below is vacuous.
    assert polluted != pristine
    assert HMCConfig.from_mapping({}).model_dump() == pristine


def test_from_mapping_ignores_a_dotenv_file(monkeypatch, tmp_path):
    """A .env in the working directory cannot reach an isolated construction."""
    (tmp_path / ".env").write_text(
        "HMC_HOST=dotenv-host.example.com\nHMC_USER=dotenvuser\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("HMC_HOST", raising=False)
    monkeypatch.delenv("HMC_USER", raising=False)

    cfg = HMCConfig.from_mapping({"host": "row-host.example.com"})

    assert cfg.host == "row-host.example.com"
    assert cfg.user == ""


def test_from_mapping_applies_every_supplied_key():
    values = {
        "host": "row-host.example.com",
        "port": 12443,
        "user": "rowuser",
        "password": "rowpass",  # pragma: allowlist secret
        "ssh_key_file": "/keys/row",
        "verify_ssl": True,
        "timeout": 15.0,
        "ssh_timeout": 30.0,
        "audit_memento": "hmc-mcp",
        "schema_version": "V1_0",
        "agent_id": "row-agent",
        "iso_url_allowlist": "iso.example.internal",
    }
    assert set(values) == set(HMCConfig.model_fields)

    cfg = HMCConfig.from_mapping(values)

    assert {name: getattr(cfg, name) for name in values} == values


def test_from_mapping_ignores_keys_that_name_no_field():
    """Matches the ``extra="ignore"`` HMCConfig already declares.

    A database row carries columns that are not settings; ``from_mapping``
    differs from ``HMCConfig(...)`` in environment isolation only.
    """
    cfg = HMCConfig.from_mapping(
        {"host": "row-host.example.com", "id": 7, "nickname": "prod"}
    )

    assert cfg.host == "row-host.example.com"


def test_from_mapping_runs_field_validators():
    """Isolation does not buy an escape from validation."""
    with pytest.raises(ValueError, match="comma"):
        HMCConfig.from_mapping({"agent_id": "bad,id"})


def test_from_mapping_runs_model_validators_once(monkeypatch):
    """The audit-memento override warning still fires, and exactly once.

    Once matters: an implementation that built an isolated instance and then
    re-validated it into an HMCConfig would emit two warnings per construction.
    """
    monkeypatch.delenv("HMC_AGENT_ID", raising=False)
    with pytest.warns(UserWarning, match="HMC_AGENT_ID is set") as caught:
        cfg = HMCConfig.from_mapping({"agent_id": "row-agent", "audit_memento": "mine"})

    assert len(caught) == 1
    assert cfg.effective_audit_memento == "hmc-mcp:row-agent"


def test_from_mapping_reports_only_the_supplied_keys_as_set():
    """model_fields_set must mean "the caller set this", not "from_mapping did".

    Passing every field explicitly is what closes the env leak, but it would
    also mark every field as caller-set. That is consumer-visible through
    ``model_dump(exclude_unset=True)`` and, more sharply, through
    ``client._verify_ssl_source``, which reads ``model_fields_set`` to name
    where ``verify_ssl`` came from in the #379 TLS audit record.
    """
    cfg = HMCConfig.from_mapping(
        {"host": "row-host.example.com", "user": "rowuser", "id": 7}
    )

    assert cfg.model_fields_set == {"host", "user"}
    assert cfg.model_dump(exclude_unset=True) == {
        "host": "row-host.example.com",
        "user": "rowuser",
    }


def test_from_mapping_keeps_the_tls_audit_provenance_accurate(monkeypatch):
    """#379's `source` names the knob to turn; from_mapping must not lie about it.

    Without the ``model_fields_set`` restoration this reports
    ``explicit-argument`` for a value nobody supplied, pointing an operator at
    an argument that does not exist.
    """
    from hmc_mcp.client import _verify_ssl_source

    monkeypatch.delenv("HMC_VERIFY_SSL", raising=False)

    assert _verify_ssl_source(HMCConfig.from_mapping({"host": "h"})) == "field-default"
    assert (
        _verify_ssl_source(HMCConfig.from_mapping({"host": "h", "verify_ssl": False}))
        == "explicit-argument"
    )


def test_from_mapping_applies_a_none_value_rather_than_defaulting():
    """A NULL column is a value, not an omission — it must not silently default."""
    assert HMCConfig.from_mapping({"ssh_key_file": None}).ssh_key_file is None
    with pytest.raises(ValueError, match="host"):
        HMCConfig.from_mapping({"host": None})


def test_from_mapping_returns_a_plain_hmcconfig():
    """Not a private subclass: ``type()`` and pydantic equality both have to hold."""
    cfg = HMCConfig.from_mapping({"host": "row-host.example.com"})

    assert type(cfg) is HMCConfig
    assert cfg == HMCConfig.from_mapping({"host": "row-host.example.com"})


def test_from_mapping_accepts_any_mapping():
    """The parameter is a Mapping, so a read-only row proxy works."""
    cfg = HMCConfig.from_mapping(MappingProxyType({"host": "row-host.example.com"}))

    assert cfg.host == "row-host.example.com"


def test_from_mapping_rejects_a_required_field_the_mapping_omits(monkeypatch):
    """A required field must fail loudly rather than fall through to the env.

    HMCConfig has no required field today. If one is ever added and
    ``from_mapping`` silently omitted it, pydantic-settings would resolve it
    from ``HMC_*`` — the exact leak this method exists to close — so the guard
    is exercised against a subclass that has one.
    """

    class RequiredFieldConfig(HMCConfig):
        tenant: str

    monkeypatch.setenv("HMC_TENANT", "leaked-tenant")

    with pytest.raises(ValueError, match="tenant"):
        RequiredFieldConfig.from_mapping({"host": "row-host.example.com"})

    supplied = RequiredFieldConfig.from_mapping(
        {"host": "row-host.example.com", "tenant": "row-tenant"}
    )
    assert supplied.tenant == "row-tenant"


def test_env_file_none_does_not_suppress_environment_variables(monkeypatch):
    """The trap #368 names: ``_env_file=None`` is not isolation.

    Pinned so docs/environment-variables.md and AGENTS.md cannot drift back to
    presenting it as one. HMCConfig declares no ``env_file`` at all, so the
    argument is inert here — it suppresses a dotenv source that was never
    configured, and never touched the environment in the first place.
    """
    monkeypatch.setenv("HMC_HOST", "leaked-host.example.com")

    assert HMCConfig(_env_file=None).host == "leaked-host.example.com"
