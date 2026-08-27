"""Tests for hmc_set_sriov_adapter_mode (SSH CLI path)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from conftest import mock_uuid_resolution

from hmc_mcp.server_tools.network import (
    hmc_set_sriov_adapter_mode as hmc_set_sriov_adapter_mode,
)

SYSTEM_UUID = "22222222-2222-4222-8222-222222222222"
SYSTEM_NAME = "Server-9080-M9S-SN12345"
ADAPTER_ID = "U78DA.001.XYZ1234-P1-C2"


def _make_ssh_mock(stdout: str = "") -> MagicMock:
    """Return a minimal asyncssh connection mock."""
    result = MagicMock()
    result.stdout = stdout

    conn = AsyncMock()
    conn.run = AsyncMock(return_value=result)
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    return conn


def _hmc_env(monkeypatch):
    """Set env vars so HMCConfig() succeeds inside the tool."""
    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "abc123")


# ---------------------------------------------------------------------- #
# Valid mode: sriov
# ---------------------------------------------------------------------- #


def test_set_sriov_mode_sriov(monkeypatch, mock_hmc):
    """The in-place tool returns unchanged after evidence-backed readback."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    fields = "adapter_id,slot_id,config_state,functional_state,phys_loc,phys_ports,logical_ports,adapter_max_logical_ports,sriov_status"
    conn_mock = _make_ssh_mock(
        f"{fields}\n{ADAPTER_ID},1,sriov,1,U,2,120,120,running\n"
    )

    with (
        patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=conn_mock),
        patch("hmc_mcp.operations.pcie.require_admitted_environment"),
    ):
        result = hmc_set_sriov_adapter_mode(SYSTEM_UUID, ADAPTER_ID, "sriov")

    command = conn_mock.run.await_args.args[0]
    assert "lshwres -r sriov --rsubtype adapter" in command
    assert "chhwres" not in command
    assert "already in sriov mode" in result


# ---------------------------------------------------------------------- #
# Valid mode: dedicated
# ---------------------------------------------------------------------- #


def test_set_sriov_mode_dedicated(monkeypatch, mock_hmc):
    """hmc_set_sriov_adapter_mode issues chhwres with mode=dedicated."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    fields = "adapter_id,slot_id,config_state,functional_state,phys_loc,phys_ports,logical_ports,adapter_max_logical_ports,sriov_status"
    conn_mock = _make_ssh_mock(
        f"{fields}\n{ADAPTER_ID},1,dedicated,1,U,2,120,120,stopped\n"
    )

    with (
        patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=conn_mock),
        patch("hmc_mcp.operations.pcie.require_admitted_environment"),
    ):
        result = hmc_set_sriov_adapter_mode(SYSTEM_UUID, ADAPTER_ID, "dedicated")

    command = conn_mock.run.await_args.args[0]
    assert "lshwres -r sriov --rsubtype adapter" in command
    assert "chhwres" not in command
    assert "already in dedicated mode" in result


# ---------------------------------------------------------------------- #
# Invalid mode: raises ValueError before SSH call
# ---------------------------------------------------------------------- #


def test_set_sriov_mode_invalid_raises(monkeypatch, mock_hmc):
    """hmc_set_sriov_adapter_mode raises ValueError for unknown mode without SSH."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    with pytest.raises(ValueError, match="Invalid mode"):
        hmc_set_sriov_adapter_mode(SYSTEM_UUID, ADAPTER_ID, "bogus")
