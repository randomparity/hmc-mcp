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
    config_dir,
    list_profiles,
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
    from hmc_mcp.config import list_nicknames
    cfg = _write_toml(tmp_path / "config.toml", NICKNAME_TOML)
    assert list_nicknames(config_path=cfg) == {"big-iron": "prod", "staging": "stg"}


def test_list_nicknames_absent(tmp_path):
    """list_nicknames returns {} when no nicknames table or no file."""
    from hmc_mcp.config import list_nicknames
    cfg = _write_toml(tmp_path / "config.toml", TWO_PROFILE_TOML)
    assert list_nicknames(config_path=cfg) == {}
    assert list_nicknames(config_path=tmp_path / "nonexistent.toml") == {}


# ---------------------------------------------------------------------------
# list_profiles_and_nicknames (issue #222)
# ---------------------------------------------------------------------------


def test_list_profiles_and_nicknames_returns_both_tables(tmp_path):
    """Both selection tables come back from one call, so they cannot disagree."""
    from hmc_mcp.config import list_profiles_and_nicknames

    cfg = _write_toml(tmp_path / "config.toml", NICKNAME_TOML)
    profiles, nicknames = list_profiles_and_nicknames(config_path=cfg)
    assert set(profiles) == {"prod", "stg"}
    assert nicknames == {"big-iron": "prod", "staging": "stg"}


def test_list_profiles_and_nicknames_absent_file(tmp_path):
    """An absent file is an empty configuration, not an error."""
    from hmc_mcp.config import list_profiles_and_nicknames

    assert list_profiles_and_nicknames(config_path=tmp_path / "nope.toml") == ([], {})


def test_list_profiles_and_nicknames_rejects_malformed_nicknames(tmp_path):
    """A malformed nicknames table raises, as it does for list_nicknames."""
    from hmc_mcp.config import list_profiles_and_nicknames

    cfg = _write_toml(
        tmp_path / "config.toml",
        NICKNAME_TOML.replace('big-iron = "prod"', "big-iron = 7"),
    )
    with pytest.raises(ConfigError, match="must map to a profile-key string"):
        list_profiles_and_nicknames(config_path=cfg)


def test_list_profiles_and_nicknames_rejects_invalid_toml(tmp_path):
    """A parse error is a ConfigError naming the path, as elsewhere in this module."""
    from hmc_mcp.config import list_profiles_and_nicknames

    cfg = tmp_path / "config.toml"
    cfg.write_text("this is [not valid toml ][[[", encoding="utf-8")
    with pytest.raises(ConfigError, match="TOML parse error"):
        list_profiles_and_nicknames(config_path=cfg)


def test_list_profiles_and_nicknames_rejects_an_unreadable_file(tmp_path):
    """An OSError must arrive as ConfigError: #222 authorizes on this reader."""
    from hmc_mcp.config import list_profiles_and_nicknames

    cfg = _write_toml(tmp_path / "config.toml", TWO_PROFILE_TOML)
    cfg.chmod(0o000)
    try:
        with pytest.raises(ConfigError, match="cannot be read"):
            list_profiles_and_nicknames(config_path=cfg)
    finally:
        cfg.chmod(0o600)


def test_list_profiles_and_nicknames_rejects_a_directory(tmp_path):
    """The exists() check is a TOCTOU and a directory satisfies it."""
    from hmc_mcp.config import list_profiles_and_nicknames

    directory = tmp_path / "config.toml"
    directory.mkdir()
    with pytest.raises(ConfigError, match="cannot be read"):
        list_profiles_and_nicknames(config_path=directory)


def test_list_profiles_and_nicknames_rejects_non_utf8(tmp_path):
    from hmc_mcp.config import list_profiles_and_nicknames

    cfg = tmp_path / "config.toml"
    cfg.write_bytes(b"\xff\xfe not utf-8")
    with pytest.raises(ConfigError, match="is not valid UTF-8"):
        list_profiles_and_nicknames(config_path=cfg)


def test_list_profiles_and_nicknames_rejects_a_non_table_profiles_key(tmp_path):
    from hmc_mcp.config import list_profiles_and_nicknames

    cfg = _write_toml(tmp_path / "config.toml", "profiles = 'not-a-table'\n")
    with pytest.raises(ConfigError, match="'profiles' must be a table"):
        list_profiles_and_nicknames(config_path=cfg)


def test_list_profiles_and_nicknames_reports_an_unresolvable_home(monkeypatch):
    """Path.home() raises under a uid with no passwd entry and no HOME."""
    from hmc_mcp.config import list_profiles_and_nicknames

    def _no_home():
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr("pathlib.Path.home", _no_home)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    with pytest.raises(ConfigError, match="cannot resolve the config path"):
        list_profiles_and_nicknames()


def test_list_profiles_and_nicknames_rejects_a_deeply_nested_document(tmp_path):
    """tomllib recurses on nested arrays; the stack runs out before the parser does."""
    from hmc_mcp.config import list_profiles_and_nicknames

    cfg = tmp_path / "config.toml"
    cfg.write_text("deep = " + "[" * 3000 + "]" * 3000, encoding="utf-8")
    with pytest.raises(ConfigError, match="document nesting is too deep"):
        list_profiles_and_nicknames(config_path=cfg)
