"""Tests for LPAR MSP flag get/set tools (SSH CLI path)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


from hmc_mcp.server import hmc_get_lpar_msp, hmc_set_lpar_msp

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
# hmc_get_lpar_msp
# ---------------------------------------------------------------------- #


def test_get_lpar_msp_runs_correct_command(monkeypatch, mock_hmc):
    """hmc_get_lpar_msp issues lssyscfg with the correct arguments."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock("1\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_get_lpar_msp(SYSTEM_UUID, LPAR_UUID)

    expected_cmd = (
        f"lssyscfg -r lpar -m {SYSTEM_NAME} "
        f"--filter lpar_names={LPAR_NAME} -F msp"
    )
    conn_mock.run.assert_called_once_with(expected_cmd, check=True, timeout=300.0)
    assert result is True


def test_get_lpar_msp_returns_true_when_enabled(monkeypatch, mock_hmc):
    """hmc_get_lpar_msp returns True when the HMC reports msp=1."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock("1\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_get_lpar_msp(SYSTEM_UUID, LPAR_UUID)

    assert result is True


def test_get_lpar_msp_returns_false_when_disabled(monkeypatch, mock_hmc):
    """hmc_get_lpar_msp returns False when the HMC reports msp=0."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock("0\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_get_lpar_msp(SYSTEM_UUID, LPAR_UUID)

    assert result is False


# ---------------------------------------------------------------------- #
# hmc_set_lpar_msp
# ---------------------------------------------------------------------- #


def test_set_lpar_msp_enabled_runs_correct_command(monkeypatch, mock_hmc):
    """hmc_set_lpar_msp with enabled=True issues chsyscfg with msp=1."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_set_lpar_msp(SYSTEM_UUID, LPAR_UUID, True)

    expected_cmd = (
        f"chsyscfg -r lpar -m {SYSTEM_NAME} "
        f"-i name={LPAR_NAME},msp=1"
    )
    conn_mock.run.assert_called_once_with(expected_cmd, check=True, timeout=300.0)
    assert result == ""


def test_set_lpar_msp_disabled_runs_correct_command(monkeypatch, mock_hmc):
    """hmc_set_lpar_msp with enabled=False issues chsyscfg with msp=0."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_set_lpar_msp(SYSTEM_UUID, LPAR_UUID, False)

    expected_cmd = (
        f"chsyscfg -r lpar -m {SYSTEM_NAME} "
        f"-i name={LPAR_NAME},msp=0"
    )
    conn_mock.run.assert_called_once_with(expected_cmd, check=True, timeout=300.0)
    assert result == ""


def test_set_lpar_msp_returns_cli_output(monkeypatch, mock_hmc):
    """hmc_set_lpar_msp returns the raw SSH stdout verbatim."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    RAW_OUTPUT = "0 objects successfully changed.\n"
    conn_mock = _make_ssh_mock(RAW_OUTPUT)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_set_lpar_msp(SYSTEM_UUID, LPAR_UUID, True)

    assert result == RAW_OUTPUT
