"""Tests for hmc-mcp config init/list/show commands (issue #125)."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from hmc_mcp import cli

RUNNER = CliRunner()

TWO_PROFILE_TOML = """\
default_profile = "prod"

[profiles.prod]
host = "hmc.example.com"
user = "admin"
password = "prodpass"  # pragma: allowlist secret

[profiles.dev]
host = "hmc-dev.example.com"
user = "devadmin"
password_env = "HMC_DEV_PW"  # pragma: allowlist secret
"""

NO_DEFAULT_TOML = """\
[profiles.alpha]
host = "hmc-alpha.example.com"
user = "admin"
password = "alphapw"  # pragma: allowlist secret

[profiles.beta]
host = "hmc-beta.example.com"
user = "admin"
password = "betapw"  # pragma: allowlist secret
"""


def _write_toml(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# config init
# ---------------------------------------------------------------------------


def test_init_creates_file(tmp_path, monkeypatch):
    """init creates the config file and prints the path when it does not exist."""
    target = tmp_path / "hmc-mcp" / "config.toml"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with patch.object(sys, "platform", "linux"):
        result = RUNNER.invoke(cli.app, ["config", "init"])
    assert result.exit_code == 0, result.output
    assert target.exists()
    # Rich may wrap long paths across lines — compare against the filename
    assert "config.toml" in result.output
    assert "hmc-mcp" in result.output


def test_init_refuses_existing_file(tmp_path, monkeypatch):
    """init exits 1 with an error message when the file already exists."""
    target = tmp_path / "hmc-mcp" / "config.toml"
    _write_toml(target, "[profiles.x]\nhost='h'\nuser='u'\npassword='p'  # pragma: allowlist secret\n")
    original_content = target.read_text()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with patch.object(sys, "platform", "linux"):
        result = RUNNER.invoke(cli.app, ["config", "init"])
    assert result.exit_code == 1
    assert "already exists" in result.output or "already exists" in (result.stderr or "")
    # File must be unchanged
    assert target.read_text() == original_content


@pytest.mark.skipif(sys.platform == "win32", reason="chmod not meaningful on Windows")
def test_init_permissions(tmp_path, monkeypatch):
    """init creates the file with mode 0o600 on POSIX."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with patch.object(sys, "platform", "linux"):
        result = RUNNER.invoke(cli.app, ["config", "init"])
    assert result.exit_code == 0, result.output
    target = tmp_path / "hmc-mcp" / "config.toml"
    mode = stat.S_IMODE(os.stat(target).st_mode)
    assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"


# ---------------------------------------------------------------------------
# config list
# ---------------------------------------------------------------------------


def test_list_shows_profiles_and_default(tmp_path, monkeypatch):
    """list shows both profile names; the default is marked."""
    _write_toml(tmp_path / "hmc-mcp" / "config.toml", TWO_PROFILE_TOML)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with patch.object(sys, "platform", "linux"):
        result = RUNNER.invoke(cli.app, ["config", "list"])
    assert result.exit_code == 0, result.output
    assert "prod" in result.output
    assert "dev" in result.output
    assert "(default)" in result.output
    # Only prod should be marked default
    lines = result.output.splitlines()
    default_lines = [ln for ln in lines if "(default)" in ln]
    assert len(default_lines) == 1
    assert "prod" in default_lines[0]


def test_list_no_default_key(tmp_path, monkeypatch):
    """list shows names without any (default) marker when default_profile absent."""
    _write_toml(tmp_path / "hmc-mcp" / "config.toml", NO_DEFAULT_TOML)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with patch.object(sys, "platform", "linux"):
        result = RUNNER.invoke(cli.app, ["config", "list"])
    assert result.exit_code == 0, result.output
    assert "alpha" in result.output
    assert "beta" in result.output
    assert "(default)" not in result.output


def test_list_absent_file(tmp_path, monkeypatch):
    """list exits 0 with a helpful message when no config file exists."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))
    with patch.object(sys, "platform", "linux"):
        result = RUNNER.invoke(cli.app, ["config", "list"])
    assert result.exit_code == 0
    output_lower = result.output.lower()
    assert "no config file" in output_lower or "config file" in output_lower


# ---------------------------------------------------------------------------
# config show
# ---------------------------------------------------------------------------


def test_show_password_redacted(tmp_path, monkeypatch):
    """show emits password_configured:True but never the literal password."""
    _write_toml(tmp_path / "hmc-mcp" / "config.toml", TWO_PROFILE_TOML)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    with patch.object(sys, "platform", "linux"):
        result = RUNNER.invoke(cli.app, ["--profile", "prod", "config", "show"])
    assert result.exit_code == 0, result.output
    assert "prodpass" not in result.output
    assert "password_configured" in result.output
    assert "True" in result.output or "true" in result.output


def test_show_password_env_not_resolved(tmp_path, monkeypatch):
    """show reports password_configured:True for password_env without resolving the var."""
    _write_toml(tmp_path / "hmc-mcp" / "config.toml", TWO_PROFILE_TOML)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    # Deliberately DO NOT set HMC_DEV_PW — show must not resolve it.
    # But load_profile() WILL try to resolve it for the HMCConfig fields.
    # So we set a dummy value: this tests that show reports the boolean,
    # not that it avoids calling load_profile entirely.
    monkeypatch.setenv("HMC_DEV_PW", "dummy-value-for-test")  # pragma: allowlist secret
    with patch.object(sys, "platform", "linux"):
        result = RUNNER.invoke(cli.app, ["--profile", "dev", "config", "show"])
    assert result.exit_code == 0, result.output
    assert "password_configured" in result.output
    assert "True" in result.output or "true" in result.output
    # Must not show the actual env var value or the password_env var name as a password
    assert "dummy-value-for-test" not in result.output  # pragma: allowlist secret


def test_show_json_flag(tmp_path, monkeypatch):
    """show --json emits valid JSON with no password key."""
    _write_toml(tmp_path / "hmc-mcp" / "config.toml", TWO_PROFILE_TOML)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    with patch.object(sys, "platform", "linux"):
        result = RUNNER.invoke(cli.app, ["--profile", "prod", "config", "show", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "password" not in data
    assert "password_configured" in data
    assert data["password_configured"] is True
    assert data["host"] == "hmc.example.com"


def test_show_unknown_profile_error(tmp_path, monkeypatch):
    """show exits 1 with a message containing the unknown profile name."""
    _write_toml(tmp_path / "hmc-mcp" / "config.toml", TWO_PROFILE_TOML)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    with patch.object(sys, "platform", "linux"):
        result = RUNNER.invoke(cli.app, ["--profile", "nonexistent", "config", "show"])
    assert result.exit_code == 1
    assert "nonexistent" in result.output


def test_show_absent_config_file_error(tmp_path, monkeypatch):
    """show exits 1 with a helpful message when no config file exists."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    with patch.object(sys, "platform", "linux"):
        result = RUNNER.invoke(cli.app, ["--profile", "prod", "config", "show"])
    assert result.exit_code == 1


def test_show_no_profile_no_default_error(tmp_path, monkeypatch):
    """show exits 1 when no --profile, no HMC_PROFILE, no default_profile in TOML."""
    _write_toml(tmp_path / "hmc-mcp" / "config.toml", NO_DEFAULT_TOML)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    monkeypatch.delenv("HMC_HOST", raising=False)
    with patch.object(sys, "platform", "linux"):
        result = RUNNER.invoke(cli.app, ["config", "show"])
    assert result.exit_code == 1


def test_show_local_profile_flag_takes_precedence(tmp_path, monkeypatch):
    """show --profile (subcommand arg) selects the profile, not global --profile."""
    _write_toml(tmp_path / "hmc-mcp" / "config.toml", TWO_PROFILE_TOML)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    # dev profile uses password_env — set a dummy value so load_profile succeeds
    monkeypatch.setenv("HMC_DEV_PW", "dummy-for-test")  # pragma: allowlist secret
    with patch.object(sys, "platform", "linux"):
        # Global --profile=prod but subcommand --profile=dev should win
        result = RUNNER.invoke(
            cli.app, ["--profile", "prod", "config", "show", "--profile", "dev"]
        )
    assert result.exit_code == 0, result.output
    assert "hmc-dev.example.com" in result.output


def test_error_message_on_failure(tmp_path, monkeypatch):
    """Config command errors produce exit code 1 and an error message."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))
    monkeypatch.delenv("HMC_PROFILE", raising=False)
    with patch.object(sys, "platform", "linux"):
        result = RUNNER.invoke(cli.app, ["--profile", "x", "config", "show"])
    assert result.exit_code == 1
    # Error message should mention Error and the profile or missing file
    assert "Error" in result.output or "error" in result.output.lower()
