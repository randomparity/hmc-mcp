"""Tests for hmc_set_sriov_adapter_mode (SSH CLI path)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hmc_mcp.server import hmc_set_sriov_adapter_mode

from conftest import mock_uuid_resolution

SYSTEM_UUID = "system-uuid-0001"
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
    """hmc_set_sriov_adapter_mode issues chhwres with mode=sriov."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    conn_mock = _make_ssh_mock("Command completed successfully.\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_set_sriov_adapter_mode(SYSTEM_UUID, ADAPTER_ID, "sriov")

    expected_cmd = (
        f"chhwres -r sriov -m {SYSTEM_NAME} -o s --id {ADAPTER_ID}"
        f" -a sriov_adapter_mode=sriov"
    )
    conn_mock.run.assert_called_once_with(expected_cmd, check=True)
    assert "completed successfully" in result


# ---------------------------------------------------------------------- #
# Valid mode: dedicated
# ---------------------------------------------------------------------- #


def test_set_sriov_mode_dedicated(monkeypatch, mock_hmc):
    """hmc_set_sriov_adapter_mode issues chhwres with mode=dedicated."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    conn_mock = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_set_sriov_adapter_mode(SYSTEM_UUID, ADAPTER_ID, "dedicated")

    expected_cmd = (
        f"chhwres -r sriov -m {SYSTEM_NAME} -o s --id {ADAPTER_ID}"
        f" -a sriov_adapter_mode=dedicated"
    )
    conn_mock.run.assert_called_once_with(expected_cmd, check=True)
    assert result == ""


# ---------------------------------------------------------------------- #
# Invalid mode: raises ValueError before SSH call
# ---------------------------------------------------------------------- #


def test_set_sriov_mode_invalid_raises(monkeypatch, mock_hmc):
    """hmc_set_sriov_adapter_mode raises ValueError for unknown mode without SSH."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    with pytest.raises(ValueError, match="Invalid mode"):
        hmc_set_sriov_adapter_mode(SYSTEM_UUID, ADAPTER_ID, "bogus")
