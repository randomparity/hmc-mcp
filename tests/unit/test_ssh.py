"""Tests for SSH passthrough (run_hmc_command) — asyncssh mocked."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import asyncssh
import pytest
from conftest import make_config

from hmc_mcp.config import HMCConfig
from hmc_mcp.errors import HMCError
from hmc_mcp.ssh.transport import HMCCLIError, run_hmc_command

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

    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=conn_mock) as mock_connect:
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

    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=conn_mock) as mock_connect:
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
    """The exact command string is forwarded to conn.run() with the SSH timeout."""
    conn_mock = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=conn_mock):
        await run_hmc_command(make_config(), "lshmc -v")

    conn_mock.run.assert_called_once_with("lshmc -v", check=True, timeout=300.0)


@pytest.mark.asyncio
async def test_run_hmc_command_command_timeout_raises_hmcclierror():
    """A command that exceeds ssh_timeout surfaces as HMCCLIError, not a hang."""
    conn_mock = _make_ssh_mock()
    conn_mock.run = AsyncMock(side_effect=TimeoutError("timed out"))

    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=conn_mock):
        with pytest.raises(HMCCLIError, match="timed out after 300s") as exc_info:
            await run_hmc_command(make_config(), "lssyscfg -r sys")

    assert isinstance(exc_info.value, HMCError)


@pytest.mark.asyncio
async def test_run_hmc_command_connect_timeout_raises_hmcclierror():
    """A connect that never completes surfaces as HMCCLIError, not a hang."""
    with patch(
        "hmc_mcp.ssh.transport.asyncssh.connect",
        side_effect=TimeoutError("timed out"),
    ), pytest.raises(HMCCLIError, match="timed out after 300s"):
        await run_hmc_command(make_config(), "lssyscfg -r sys")


@pytest.mark.asyncio
async def test_run_hmc_command_honours_custom_ssh_timeout():
    """conn.run receives the configured ssh_timeout, not the hardcoded default."""
    conn_mock = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=conn_mock):
        await run_hmc_command(make_config(ssh_timeout=45.0), "lssyscfg -r sys")

    conn_mock.run.assert_called_once_with("lssyscfg -r sys", check=True, timeout=45.0)


@pytest.mark.asyncio
async def test_run_hmc_command_nonzero_exit_raises_hmcclierror():
    """A non-zero command exit surfaces as HMCCLIError with stderr included.

    run_hmc_command translates asyncssh.ProcessError so callers never need to
    import the SSH library just to catch the exception type.
    """
    conn_mock = _make_ssh_mock()
    conn_mock.run = AsyncMock(
        side_effect=asyncssh.ProcessError(
            env={},
            command="lssyscfg -r sys",
            subsystem=None,
            exit_status=1,
            exit_signal=None,
            returncode=1,
            stdout="",
            stderr="HSCL0001 bad config",
        )
    )

    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=conn_mock):
        with pytest.raises(HMCCLIError, match="HSCL0001 bad config") as exc_info:
            await run_hmc_command(make_config(), "lssyscfg -r sys")

    # HMCCLIError subclasses HMCError so REST and CLI failures share one type.
    assert isinstance(exc_info.value, HMCError)
    assert "lssyscfg -r sys" in str(exc_info.value)
    assert "exit status 1" in str(exc_info.value)


@pytest.mark.asyncio
async def test_run_hmc_command_signal_failure_names_signal_and_command():
    conn_mock = _make_ssh_mock()
    conn_mock.run = AsyncMock(
        side_effect=asyncssh.ProcessError(
            env={},
            command="lssyscfg -r sys",
            subsystem=None,
            exit_status=None,
            exit_signal="TERM",
            returncode=-15,
            stdout="",
            stderr="terminated",
        )
    )

    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=conn_mock):
        with pytest.raises(HMCCLIError) as exc_info:
            await run_hmc_command(make_config(), "lssyscfg -r sys")

    message = str(exc_info.value)
    assert "lssyscfg -r sys" in message
    assert "signal TERM" in message
    assert "exit status None" not in message


@pytest.mark.asyncio
async def test_run_hmc_command_connect_error_raises_hmcclierror():
    """An SSH connection/auth failure surfaces as HMCCLIError, not a raw
    asyncssh error."""
    with patch(
        "hmc_mcp.ssh.transport.asyncssh.connect",
        side_effect=asyncssh.Error("connect", "connection refused"),
    ), pytest.raises(HMCCLIError, match="connection refused"):
        await run_hmc_command(make_config(), "lssyscfg -r sys")


def test_hmc_config_ssh_key_field_default():
    """ssh_key_file defaults to None when env var is not set."""
    cfg = HMCConfig(host="h", user="u", password="p")
    assert cfg.ssh_key_file is None


def test_hmc_config_ssh_key_field_set():
    """ssh_key_file is accepted via constructor (mirrors env var mapping)."""
    cfg = HMCConfig(host="h", user="u", password="p", ssh_key_file="/tmp/key")
    assert cfg.ssh_key_file == "/tmp/key"


def test_hmc_config_ssh_timeout_default():
    """ssh_timeout defaults to 300s and honours an explicit override."""
    assert HMCConfig(host="h", user="u", password="p").ssh_timeout == 300.0
    assert HMCConfig(host="h", user="u", password="p", ssh_timeout=45.0).ssh_timeout == 45.0


# ---------------------------------------------------------------------------
# Credential validation parity with the REST path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_hmc_command_missing_config_fails_actionably():
    """Password auth without host/user raises the shared actionable error
    instead of an obscure asyncssh error.

    The suite fixture clears ambient HMC credentials before this test.
    """
    with patch("hmc_mcp.ssh.transport.asyncssh.connect") as mock_connect:
        with pytest.raises(ValueError, match="Missing HMC configuration"):
            await run_hmc_command(HMCConfig(), "lssyscfg -r sys")
    mock_connect.assert_not_called()


@pytest.mark.asyncio
async def test_run_hmc_command_key_auth_skips_password_requirement():
    """Key auth does not require a password, so missing password passes the
    credential check and the command reaches asyncssh."""
    conn_mock = _make_ssh_mock("lssyscfg key output\n")

    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=conn_mock) as mock_connect:
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


def test_validate_credentials_password_still_required_by_default(monkeypatch):
    """validate_credentials() raises when password is absent.

    The suite fixture clears ambient HMC credentials before this test.
    """
    monkeypatch.delenv("HMC_PASSWORD", raising=False)
    with pytest.raises(ValueError, match="password"):
        HMCConfig(host="h", user="u").validate_credentials()
