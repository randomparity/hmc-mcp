"""Tests for per-call profile routing in SSH-backed MCP tools (issue #127).

These tests verify that:
1. `_ssh_with_client` threads a caller-supplied profile to the SSH config and
   both name resolvers.
2. `_resolve_system_name` / `_resolve_lpar_name` call `client_from_env(profile)`
   instead of `client_from_env()`.
3. `run_hmc_cli` passes a pre-built HMCConfig to `run_hmc_command` when supplied.
4. MCP tool `profile` parameter reaches the SSH connection.
5. `profile=None` preserves existing env-default behavior.
"""

from __future__ import annotations

import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from hmc_mcp.config import HMCConfig
from hmc_mcp.ssh import run_hmc_cli

from conftest import mock_uuid_resolution

# ---------------------------------------------------------------------------
# Shared test constants
# ---------------------------------------------------------------------------

SYSTEM_UUID = "22222222-2222-4222-8222-222222222222"
SYSTEM_NAME = "Server-9080-M9S-SN12345"
LPAR_UUID = "11111111-1111-4111-8111-111111111111"
LPAR_NAME = "my-lpar"

DEV_HOST = "dev-hmc.example.com"
DEV_USER = "devuser"
DEV_PASSWORD = "devpass"  # pragma: allowlist secret

PROD_HOST = "prod-hmc.example.com"
PROD_USER = "produser"
PROD_PASSWORD = "prodpass"  # pragma: allowlist secret

DEV_CONFIG = HMCConfig(host=DEV_HOST, user=DEV_USER, password=DEV_PASSWORD)
PROD_CONFIG = HMCConfig(host=PROD_HOST, user=PROD_USER, password=PROD_PASSWORD)


def _make_ssh_mock(stdout: str = "") -> MagicMock:
    """Return a minimal asyncssh connection mock."""
    result = MagicMock()
    result.stdout = stdout
    conn = AsyncMock()
    conn.run = AsyncMock(return_value=result)
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    return conn


def _set_env(monkeypatch, host: str, user: str, password: str) -> None:
    """Set HMC_* env vars to make HMCConfig() resolve without TOML."""
    monkeypatch.setenv("HMC_HOST", host)
    monkeypatch.setenv("HMC_USER", user)
    monkeypatch.setenv("HMC_PASSWORD", password)


# ---------------------------------------------------------------------------
# Task 1.3 — run_hmc_cli config passthrough
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_hmc_cli_uses_supplied_config():
    """run_hmc_cli(cmd, config=...) passes the supplied config to run_hmc_command."""
    conn = _make_ssh_mock("output")
    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn) as mock_connect:
        await run_hmc_cli("lshmc -v", DEV_CONFIG)

    call_kwargs = mock_connect.call_args.kwargs
    assert call_kwargs["host"] == DEV_HOST
    assert call_kwargs["username"] == DEV_USER
    assert call_kwargs["password"] == DEV_PASSWORD


@pytest.mark.asyncio
async def test_run_hmc_cli_no_config_uses_hmcconfig(monkeypatch):
    """run_hmc_cli(cmd) with no config falls back to HMCConfig() from env."""
    _set_env(monkeypatch, PROD_HOST, PROD_USER, PROD_PASSWORD)
    conn = _make_ssh_mock("output")
    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn) as mock_connect:
        await run_hmc_cli("lshmc -v")

    call_kwargs = mock_connect.call_args.kwargs
    assert call_kwargs["host"] == PROD_HOST


# ---------------------------------------------------------------------------
# Task 1.1 — _ssh_with_client profile threading
# ---------------------------------------------------------------------------


def test_ssh_with_client_profile_reaches_ssh(monkeypatch, mock_hmc):
    """_ssh_with_client with profile routes SSH to the profile's HMC host."""
    # mock_hmc router handles REST resolution for system UUID
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)

    # Stub client_from_env to return a client backed by DEV_CONFIG when
    # profile="dev" is supplied.
    with patch("hmc_mcp._app.client_from_env") as mock_cfe:
        mock_cfe.return_value.config = DEV_CONFIG
        conn = _make_ssh_mock("")
        with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn) as mock_connect:
            from hmc_mcp._app import _ssh_with_client
            from hmc_mcp.ssh import list_memory_pools

            _ssh_with_client(
                lambda config, system_name, _: list_memory_pools(config, system_name),
                system_name_or_uuid=SYSTEM_NAME,  # plain name → no UUID resolution
                profile="dev",
            )

    mock_cfe.assert_called_once_with("dev")
    call_kwargs = mock_connect.call_args.kwargs
    assert call_kwargs["host"] == DEV_HOST


def test_ssh_with_client_profile_none_uses_env(monkeypatch, mock_hmc):
    """_ssh_with_client with profile=None uses env-default credentials."""
    _set_env(monkeypatch, PROD_HOST, PROD_USER, PROD_PASSWORD)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)

    conn = _make_ssh_mock("")
    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn) as mock_connect:
        from hmc_mcp._app import _ssh_with_client
        from hmc_mcp.ssh import list_memory_pools

        _ssh_with_client(
            lambda config, system_name, _: list_memory_pools(config, system_name),
            system_name_or_uuid=SYSTEM_NAME,
            # no profile argument — should default to None → env fallback
        )

    call_kwargs = mock_connect.call_args.kwargs
    assert call_kwargs["host"] == PROD_HOST


# ---------------------------------------------------------------------------
# Task 1.2 — _resolve_system_name / _resolve_lpar_name with profile
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_system_name_uses_profile(mock_hmc):
    """_resolve_system_name calls client_from_env(profile) for the REST leg."""
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)

    with patch("hmc_mcp._app.client_from_env") as mock_cfe:
        # Make client_from_env("dev") return an async context manager that
        # returns a client whose get_managed_system we can control.
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get_managed_system = AsyncMock(
            return_value={"Resource": {"SystemName": SYSTEM_NAME}}
        )
        mock_cfe.return_value = mock_client

        from hmc_mcp._app import _resolve_system_name

        config = HMCConfig(host=DEV_HOST, user=DEV_USER, password=DEV_PASSWORD)
        result = await _resolve_system_name(config, SYSTEM_UUID, "dev")

    mock_cfe.assert_called_once_with("dev")
    assert result == SYSTEM_NAME


@pytest.mark.asyncio
async def test_resolve_system_name_profile_none_uses_default():
    """_resolve_system_name with profile=None passes None to client_from_env."""
    with patch("hmc_mcp._app.client_from_env") as mock_cfe:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get_managed_system = AsyncMock(
            return_value={"Resource": {"SystemName": SYSTEM_NAME}}
        )
        mock_cfe.return_value = mock_client

        from hmc_mcp._app import _resolve_system_name

        config = HMCConfig(host=DEV_HOST, user=DEV_USER, password=DEV_PASSWORD)
        await _resolve_system_name(config, SYSTEM_UUID)  # no profile arg → None

    mock_cfe.assert_called_once_with(None)


@pytest.mark.asyncio
async def test_resolve_lpar_name_uses_profile():
    """_resolve_lpar_name calls client_from_env(profile) for the REST leg."""
    with patch("hmc_mcp._app.client_from_env") as mock_cfe:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get_logical_partition = AsyncMock(
            return_value={"Resource": {"PartitionName": LPAR_NAME}}
        )
        mock_cfe.return_value = mock_client

        from hmc_mcp._app import _resolve_lpar_name

        config = HMCConfig(host=DEV_HOST, user=DEV_USER, password=DEV_PASSWORD)
        result = await _resolve_lpar_name(config, LPAR_UUID, profile="dev")

    mock_cfe.assert_called_once_with("dev")
    assert result == LPAR_NAME


# ---------------------------------------------------------------------------
# SSH fallback uses same config when REST transport fails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_system_name_ssh_fallback_uses_supplied_config():
    """When the REST leg raises httpx.HTTPError, the SSH fallback uses the supplied config.

    The config passed to the fallback is the same one given to _resolve_system_name,
    so a caller that built config from client_from_env(profile).config ensures the
    fallback also uses the profile's credentials.
    """
    from hmc_mcp._app import _resolve_system_name

    fallback_output = f"{SYSTEM_UUID},{SYSTEM_NAME}\n"

    with patch("hmc_mcp._app.client_from_env") as mock_cfe:
        # REST leg raises a transport error → SSH fallback runs
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get_managed_system = AsyncMock(
            side_effect=httpx.ConnectError("unreachable")
        )
        mock_cfe.return_value = mock_client

        conn = _make_ssh_mock(fallback_output)
        with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn) as mock_connect:
            result = await _resolve_system_name(DEV_CONFIG, SYSTEM_UUID, "dev")

    # SSH fallback used the config we supplied (DEV_CONFIG)
    assert result == SYSTEM_NAME
    assert mock_connect.call_args.kwargs["host"] == DEV_HOST


# ---------------------------------------------------------------------------
# Task 1.4 — MCP tool profile parameter threads to SSH
# ---------------------------------------------------------------------------


def test_hmc_run_command_profile_reaches_ssh(monkeypatch):
    """hmc_run_command(cmd, profile=...) routes SSH to the profile's HMC host."""
    from hmc_mcp.server import hmc_run_command

    # server_system.py imports client_from_env from hmc_mcp.common
    with patch("hmc_mcp.server_system.client_from_env") as mock_cfe:
        mock_cfe.return_value.config = DEV_CONFIG
        conn = _make_ssh_mock("output")
        with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn) as mock_connect:
            hmc_run_command("lshmc -v", profile="dev")

    mock_cfe.assert_called_once_with("dev")
    assert mock_connect.call_args.kwargs["host"] == DEV_HOST


def test_hmc_restore_vios_profile_reaches_ssh(monkeypatch):
    """hmc_restore_vios with profile routes SSH to the profile's HMC host."""
    from hmc_mcp.server import hmc_restore_vios

    # server_vios.py imports client_from_env from hmc_mcp.common
    with patch("hmc_mcp.server_vios.client_from_env") as mock_cfe:
        mock_cfe.return_value.config = DEV_CONFIG
        conn = _make_ssh_mock("")
        with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn) as mock_connect:
            hmc_restore_vios(SYSTEM_UUID, "backup.tar.gz", profile="dev")

    mock_cfe.assert_called_once_with("dev")
    assert mock_connect.call_args.kwargs["host"] == DEV_HOST


def test_hmc_list_memory_pools_profile_reaches_ssh(monkeypatch, mock_hmc):
    """hmc_list_memory_pools with profile threads profile through _ssh_with_client."""
    from hmc_mcp.server import hmc_list_memory_pools

    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)

    with patch("hmc_mcp._app.client_from_env") as mock_cfe:
        mock_cfe.return_value.config = DEV_CONFIG
        conn = _make_ssh_mock("")
        with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn) as mock_connect:
            hmc_list_memory_pools(SYSTEM_NAME, profile="dev")

    mock_cfe.assert_called_once_with("dev")
    assert mock_connect.call_args.kwargs["host"] == DEV_HOST


def test_different_profiles_produce_independent_configs():
    """Two calls with different profiles use independent HMCConfig values.

    Verifies that profile selection is call-local with no shared state.
    """
    configs_seen = []

    def capture_connect(**kwargs):
        configs_seen.append(kwargs["host"])
        raise Exception("abort after capture")  # abort the SSH connection attempt

    with patch("hmc_mcp._app.client_from_env") as mock_cfe:
        # First call: profile="dev" → DEV_CONFIG
        mock_cfe.return_value.config = DEV_CONFIG
        with patch("hmc_mcp.ssh.asyncssh.connect", side_effect=capture_connect):
            try:
                from hmc_mcp._app import _ssh_with_client
                from hmc_mcp.ssh import list_memory_pools
                _ssh_with_client(
                    lambda config, system_name, _: list_memory_pools(config, system_name),
                    system_name_or_uuid=SYSTEM_NAME,
                    profile="dev",
                )
            except Exception:
                pass

        # Second call: profile="prod" → PROD_CONFIG
        mock_cfe.return_value.config = PROD_CONFIG
        with patch("hmc_mcp.ssh.asyncssh.connect", side_effect=capture_connect):
            try:
                _ssh_with_client(
                    lambda config, system_name, _: list_memory_pools(config, system_name),
                    system_name_or_uuid=SYSTEM_NAME,
                    profile="prod",
                )
            except Exception:
                pass

    assert configs_seen[0] == DEV_HOST
    assert configs_seen[1] == PROD_HOST
