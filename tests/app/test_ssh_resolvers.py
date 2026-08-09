"""Tests for the SSH-tool name resolvers (REST-first, SSH fallback).

The SSH-passthrough tools take a system / LPAR that may be given by CLI
name or by UUID.  Names pass through untouched; UUIDs are resolved to their
CLI names via REST, falling back to an ``lssyscfg`` name lookup over SSH
when the REST transport is unreachable.  These tests pin that contract:

- the SSH lookup primitives parse ``lssyscfg -F UUID,<name>`` output;
- a name argument never touches REST or SSH;
- a transport failure (``httpx.HTTPError``) triggers the SSH fallback;
- a REST status error (``HMCError``) does *not* — REST answered, so the
  unknown-UUID error surfaces instead of silently guessing via SSH.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from hmc_mcp._app import _resolve_lpar_name, _resolve_system_name
from hmc_mcp.errors import HMCError
from hmc_mcp.ssh import HMCCLIError, _ssh_lpar_name, _ssh_system_name

from conftest import make_config

SYSTEM_UUID = "22222222-2222-4222-8222-222222222222"
SYSTEM_NAME = "Server-9080-M9S-SN12345"
LPAR_UUID = "11111111-1111-4111-8111-111111111111"
LPAR_NAME = "my-lpar"

# ``lssyscfg -F UUID,SystemName`` / ``-F UUID,PartitionName`` output rows.
_SYS_ROWS = f"00000000-0000-0000-0000-000000000000,other\n{SYSTEM_UUID},{SYSTEM_NAME}\n"
_LPAR_ROWS = f"{LPAR_UUID},{LPAR_NAME}\n"


def _make_ssh_mock(stdout: str = "") -> MagicMock:
    """Return a mock asyncssh connection whose run() returns stdout."""
    result = MagicMock()
    result.stdout = stdout

    conn = AsyncMock()
    conn.run = AsyncMock(return_value=result)
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    return conn


def _hmc_env(monkeypatch) -> None:
    """Set env vars so ``client_from_env()`` inside the resolvers succeeds."""
    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "abc123")


# ---------------------------------------------------------------------- #
# ssh.py lookup primitives
# ---------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ssh_system_name_parses_matching_row():
    """_ssh_system_name returns the name on the matching UUID,SystemName row."""
    conn = _make_ssh_mock(_SYS_ROWS)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn):
        name = await _ssh_system_name(make_config(), SYSTEM_UUID)

    assert name == SYSTEM_NAME
    cmd = conn.run.call_args[0][0]
    assert "lssyscfg -r sys -F UUID,SystemName" in cmd


@pytest.mark.asyncio
async def test_ssh_system_name_raises_when_uuid_missing():
    """A UUID with no matching row raises HMCCLIError, not a silent guess."""
    conn = _make_ssh_mock("00000000-0000-0000-0000-000000000000,other\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn):
        with pytest.raises(HMCCLIError, match="Could not resolve system UUID"):
            await _ssh_system_name(make_config(), SYSTEM_UUID)


@pytest.mark.asyncio
async def test_ssh_lpar_name_scopes_to_system():
    """_ssh_lpar_name scopes lssyscfg -r lpar with -m when a system is given."""
    conn = _make_ssh_mock(_LPAR_ROWS)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn):
        name = await _ssh_lpar_name(make_config(), LPAR_UUID, system_name=SYSTEM_NAME)

    assert name == LPAR_NAME
    cmd = conn.run.call_args[0][0]
    assert f"-m {SYSTEM_NAME}" in cmd
    assert "lssyscfg -r lpar" in cmd
    assert " -F UUID,PartitionName" in cmd


@pytest.mark.asyncio
async def test_ssh_lpar_name_unscoped_without_system():
    """Without a system name the lookup spans all managed systems (no -m)."""
    conn = _make_ssh_mock(_LPAR_ROWS)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn):
        name = await _ssh_lpar_name(make_config(), LPAR_UUID)

    assert name == LPAR_NAME
    cmd = conn.run.call_args[0][0]
    assert "-m " not in cmd


# ---------------------------------------------------------------------- #
# _resolve_system_name / _resolve_lpar_name (REST-first, SSH fallback)
# ---------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_resolve_system_name_passes_names_through():
    """A plain name is returned untouched — no REST session, no SSH command."""
    with patch("hmc_mcp._app.client_from_env") as mock_client, patch(
        "hmc_mcp.ssh.asyncssh.connect"
    ) as mock_connect:
        name = await _resolve_system_name(make_config(), SYSTEM_NAME)

    assert name == SYSTEM_NAME
    mock_client.assert_not_called()
    mock_connect.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_lpar_name_passes_names_through():
    """A plain LPAR name is returned untouched."""
    with patch("hmc_mcp._app.client_from_env") as mock_client, patch(
        "hmc_mcp.ssh.asyncssh.connect"
    ) as mock_connect:
        name = await _resolve_lpar_name(make_config(), LPAR_NAME, SYSTEM_NAME)

    assert name == LPAR_NAME
    mock_client.assert_not_called()
    mock_connect.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_system_name_falls_back_to_ssh_when_rest_down(monkeypatch, mock_hmc):
    """A REST transport failure triggers the SSH name lookup."""
    _hmc_env(monkeypatch)
    # REST is unreachable: logon raises a transport error (not an HMCError).
    mock_hmc.put("/rest/api/web/Logon").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    conn = _make_ssh_mock(_SYS_ROWS)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn):
        name = await _resolve_system_name(make_config(), SYSTEM_UUID)

    assert name == SYSTEM_NAME


@pytest.mark.asyncio
async def test_resolve_lpar_name_falls_back_scoped_by_system(monkeypatch, mock_hmc):
    """The LPAR SSH fallback is scoped to the resolved system name."""
    _hmc_env(monkeypatch)
    mock_hmc.put("/rest/api/web/Logon").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    conn = _make_ssh_mock(_LPAR_ROWS)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn):
        name = await _resolve_lpar_name(
            make_config(), LPAR_UUID, system_name=SYSTEM_NAME
        )

    assert name == LPAR_NAME
    cmd = conn.run.call_args[0][0]
    assert f"-m {SYSTEM_NAME}" in cmd


@pytest.mark.asyncio
async def test_resolve_system_name_does_not_fall_back_on_rest_status_error(
    monkeypatch, mock_hmc
):
    """A REST 4xx (HMCError) is a real answer — no SSH fallback.

    REST responded, so the unknown UUID should surface as an error rather
    than silently resolving via SSH.
    """
    _hmc_env(monkeypatch)
    # logon succeeds (fixture default); the system GET returns 404.
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}").mock(
        return_value=httpx.Response(404, text="not found")
    )

    with patch("hmc_mcp.ssh.asyncssh.connect") as mock_connect:
        with pytest.raises(HMCError):
            await _resolve_system_name(make_config(), SYSTEM_UUID)

    mock_connect.assert_not_called()
