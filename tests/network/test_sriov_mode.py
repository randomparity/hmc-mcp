"""Tests for hmc_set_sriov_adapter_mode (SSH CLI path)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from conftest import live_fixture, mock_uuid_resolution

from hmcpctl.operations.virtualization.pcie import SriovLogicalPortCapabilityError
from hmcpctl.server_tools.virtualization.pcie import (
    hmc_set_sriov_adapter_mode,
)

SYSTEM_UUID = "22222222-2222-4222-8222-222222222222"
SYSTEM_NAME = "Server-9080-M9S-SN12345"
ADAPTER_ID = "1"
# V10R3 lists an SR-IOV-mode adapter by its numeric adapter_id and prints
# `null` for the adapter_id of a dedicated-mode one (#1202).
ADAPTERS = live_fixture("cli-sriov-adapters")["stdout"]


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
    conn_mock = _make_ssh_mock(ADAPTERS)

    with (
        patch("hmcpctl.ssh.transport.asyncssh.connect", return_value=conn_mock),
        patch("hmcpctl.operations.virtualization.pcie.require_admitted_environment"),
    ):
        result = hmc_set_sriov_adapter_mode(SYSTEM_UUID, ADAPTER_ID, "sriov")

    command = conn_mock.run.await_args.args[0]
    assert "lshwres -r sriov --rsubtype adapter" in command
    assert "chhwres" not in command
    assert result == "Adapter 1 already in sriov mode"


# ---------------------------------------------------------------------- #
# Valid mode: dedicated
# ---------------------------------------------------------------------- #


def test_set_sriov_mode_dedicated_refuses_the_transition(monkeypatch, mock_hmc):
    """An SR-IOV-mode adapter asked for dedicated mode fails closed, unmutated."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    conn_mock = _make_ssh_mock(ADAPTERS)

    with (
        patch("hmcpctl.ssh.transport.asyncssh.connect", return_value=conn_mock),
        patch("hmcpctl.operations.virtualization.pcie.require_admitted_environment"),
        pytest.raises(SriovLogicalPortCapabilityError, match="not admitted"),
    ):
        hmc_set_sriov_adapter_mode(SYSTEM_UUID, ADAPTER_ID, "dedicated")

    assert "chhwres" not in conn_mock.run.await_args.args[0]


@pytest.mark.parametrize("adapter_id", ["null", "0", "U1-P1-C2"])
def test_set_sriov_mode_refuses_a_non_numeric_adapter_id(
    monkeypatch, mock_hmc, adapter_id
):
    """`null` is the HMC's absent marker, not an adapter a caller can select."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    conn_mock = _make_ssh_mock(ADAPTERS)

    with (
        patch("hmcpctl.ssh.transport.asyncssh.connect", return_value=conn_mock),
        patch("hmcpctl.operations.virtualization.pcie.require_admitted_environment"),
        pytest.raises(ValueError, match="positive decimal"),
    ):
        hmc_set_sriov_adapter_mode(SYSTEM_UUID, adapter_id, "dedicated")

    conn_mock.run.assert_not_awaited()


# ---------------------------------------------------------------------- #
# Invalid mode: raises ValueError before SSH call
# ---------------------------------------------------------------------- #


def test_set_sriov_mode_invalid_raises(monkeypatch, mock_hmc):
    """hmc_set_sriov_adapter_mode raises ValueError for unknown mode without SSH."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    with pytest.raises(ValueError, match="Invalid mode"):
        hmc_set_sriov_adapter_mode(SYSTEM_UUID, ADAPTER_ID, "bogus")
