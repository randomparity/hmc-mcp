"""Tests for SSH passthrough (run_hmc_command) — asyncssh mocked."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hmc_mcp.config import HMCConfig
from hmc_mcp.ssh import run_hmc_command


def make_config(**kw) -> HMCConfig:
    return HMCConfig(host="hmc.test", user="hscroot", password="abc123", **kw)


# ---------------------------------------------------------------------------
# Helpers to build a minimal asyncssh mock
# ---------------------------------------------------------------------------

def _make_ssh_mock(stdout: str = "output\n") -> MagicMock:
    """Return a mock asyncssh connection whose run() returns stdout."""
    result = MagicMock()
    result.stdout = stdout

    conn = AsyncMock()
    conn.run = AsyncMock(return_value=result)
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    return conn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_hmc_command_password_auth():
    """Password auth path: asyncssh.connect called with password, no client_keys."""
    conn_mock = _make_ssh_mock("lssyscfg output\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock) as mock_connect:
        result = await run_hmc_command(make_config(), "lssyscfg -r sys")

    mock_connect.assert_called_once()
    call_kwargs = mock_connect.call_args.kwargs
    assert call_kwargs["host"] == "hmc.test"
    assert call_kwargs["username"] == "hscroot"
    assert call_kwargs["password"] == "abc123"
    assert "client_keys" not in call_kwargs
    assert result == "lssyscfg output\n"


@pytest.mark.asyncio
async def test_run_hmc_command_key_auth():
    """Key auth path: asyncssh.connect called with client_keys, password=None."""
    conn_mock = _make_ssh_mock("lssyscfg key output\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock) as mock_connect:
        result = await run_hmc_command(
            make_config(ssh_key_file="/home/user/.ssh/hmc_key"),
            "lssyscfg -r sys",
        )

    call_kwargs = mock_connect.call_args.kwargs
    assert call_kwargs["client_keys"] == ["/home/user/.ssh/hmc_key"]
    assert call_kwargs["password"] is None
    assert result == "lssyscfg key output\n"


@pytest.mark.asyncio
async def test_run_hmc_command_passes_cmd():
    """The exact command string is forwarded to conn.run()."""
    conn_mock = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        await run_hmc_command(make_config(), "lshmc -v")

    conn_mock.run.assert_called_once_with("lshmc -v", check=True)


def test_hmc_config_ssh_key_field_default():
    """ssh_key_file defaults to None when env var is not set."""
    cfg = HMCConfig(host="h", user="u", password="p")
    assert cfg.ssh_key_file is None


def test_hmc_config_ssh_key_field_set():
    """ssh_key_file is accepted via constructor (mirrors env var mapping)."""
    cfg = HMCConfig(host="h", user="u", password="p", ssh_key_file="/tmp/key")
    assert cfg.ssh_key_file == "/tmp/key"


# ---------------------------------------------------------------------------
# Credential validation parity with the REST path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_hmc_command_missing_config_fails_actionably(monkeypatch):
    """Password auth without host/user raises the shared actionable error
    instead of an obscure asyncssh error."""
    for var in ("HMC_HOST", "HMC_USER", "HMC_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    with patch("hmc_mcp.ssh.asyncssh.connect") as mock_connect:
        with pytest.raises(ValueError, match="Missing HMC configuration"):
            await run_hmc_command(HMCConfig(), "lssyscfg -r sys")
    mock_connect.assert_not_called()


@pytest.mark.asyncio
async def test_run_hmc_command_key_auth_skips_password_requirement():
    """Key auth does not require a password, so missing password passes the
    credential check and the command reaches asyncssh."""
    conn_mock = _make_ssh_mock("lssyscfg key output\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock) as mock_connect:
        result = await run_hmc_command(
            HMCConfig(host="hmc.test", user="hscroot", ssh_key_file="/home/user/.ssh/hmc_key"),
            "lssyscfg -r sys",
        )

    call_kwargs = mock_connect.call_args.kwargs
    assert call_kwargs["client_keys"] == ["/home/user/.ssh/hmc_key"]
    assert call_kwargs["password"] is None
    assert result == "lssyscfg key output\n"


def test_validate_credentials_key_auth_skips_password():
    """require_password=False omits the password from the missing list."""
    HMCConfig(host="h", user="u").validate_credentials(require_password=False)


def test_validate_credentials_password_still_required_by_default():
    with pytest.raises(ValueError, match="password"):
        HMCConfig(host="h", user="u").validate_credentials()
