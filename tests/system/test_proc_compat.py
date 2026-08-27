"""Tests for LPAR processor compatibility mode tools (SSH CLI path)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


from hmc_mcp.server import (
    hmc_get_proc_compat_modes,
    hmc_get_lpar_proc_compat,
    hmc_set_lpar_proc_compat,
)

from conftest import mock_uuid_resolution

SYSTEM_UUID = "22222222-2222-4222-8222-222222222222"
SYSTEM_NAME = "Server-9080-M9S-SN123456"
LPAR_UUID = "11111111-1111-4111-8111-111111111111"
LPAR_NAME = "test-lpar-01"


def _make_ssh_mock(stdout: str = "") -> MagicMock:
    """Return a minimal asyncssh connection mock."""
    result = MagicMock()
    result.stdout = stdout

    conn = AsyncMock()
    conn.run = AsyncMock(return_value=result)
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    return conn


def _hmc_env(monkeypatch) -> None:
    """Set env vars so HMCConfig() resolves inside the tool."""
    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "abc123")


# ---------------------------------------------------------------------- #
# hmc_get_proc_compat_modes
# ---------------------------------------------------------------------- #


def test_get_proc_compat_modes_runs_correct_command(monkeypatch, mock_hmc):
    """hmc_get_proc_compat_modes issues lssyscfg sys with correct arguments."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    conn_mock = _make_ssh_mock("default,POWER8,POWER9,POWER10\n")

    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=conn_mock):
        result = hmc_get_proc_compat_modes(SYSTEM_UUID)

    expected_cmd = f"lssyscfg -r sys -m {SYSTEM_NAME} -F lpar_proc_compat_modes"
    conn_mock.run.assert_awaited_with(expected_cmd, check=True, timeout=300.0)
    assert result == ["default", "POWER8", "POWER9", "POWER10"]


def test_get_proc_compat_modes_returns_empty_when_none(monkeypatch, mock_hmc):
    """hmc_get_proc_compat_modes handles empty outputs gracefully."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    conn_mock = _make_ssh_mock("\n")

    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=conn_mock):
        result = hmc_get_proc_compat_modes(SYSTEM_UUID)

    assert result == []


# ---------------------------------------------------------------------- #
# hmc_get_lpar_proc_compat
# ---------------------------------------------------------------------- #


def test_get_lpar_proc_compat_runs_correct_command(monkeypatch, mock_hmc):
    """hmc_get_lpar_proc_compat issues lssyscfg lpar with correct arguments."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock("POWER9,POWER8\n")

    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=conn_mock):
        result = hmc_get_lpar_proc_compat(SYSTEM_UUID, LPAR_UUID)

    expected_cmd = (
        f"lssyscfg -r lpar -m {SYSTEM_NAME} --filter lpar_names={LPAR_NAME} "
        "-F desired_lpar_proc_compat_mode,curr_lpar_proc_compat_mode"
    )
    conn_mock.run.assert_called_once_with(expected_cmd, check=True, timeout=300.0)
    assert result == {"desired": "POWER9", "curr": "POWER8"}


def test_get_lpar_proc_compat_handles_empty_output(monkeypatch, mock_hmc):
    """hmc_get_lpar_proc_compat handles empty CLI output correctly."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock("\n")

    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=conn_mock):
        result = hmc_get_lpar_proc_compat(SYSTEM_UUID, LPAR_UUID)

    assert result == {"desired": "", "curr": ""}


# ---------------------------------------------------------------------- #
# hmc_set_lpar_proc_compat
# ---------------------------------------------------------------------- #


def test_set_lpar_proc_compat_runs_correct_command(monkeypatch, mock_hmc):
    """hmc_set_lpar_proc_compat issues chsyscfg with correct arguments."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=conn_mock):
        result = hmc_set_lpar_proc_compat(SYSTEM_UUID, LPAR_UUID, "POWER9")

    expected_cmd = (
        f"chsyscfg -r lpar -m {SYSTEM_NAME} "
        f"-i name={LPAR_NAME},lpar_proc_compat_mode=POWER9"
    )
    conn_mock.run.assert_awaited_with(expected_cmd, check=True, timeout=300.0)
    assert result == ""
