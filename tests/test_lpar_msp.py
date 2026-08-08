"""Tests for LPAR MSP flag get/set tools (SSH CLI path)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hmc_mcp.server import hmc_get_lpar_msp, hmc_set_lpar_msp

LPAR_NAME = "test-lpar-01"
SYSTEM_NAME = "Server-9080-M9S-SN123456"


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


def test_get_lpar_msp_runs_correct_command(monkeypatch):
    """hmc_get_lpar_msp issues lssyscfg with the correct arguments."""
    _hmc_env(monkeypatch)
    conn_mock = _make_ssh_mock("1\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_get_lpar_msp(LPAR_NAME, SYSTEM_NAME)

    expected_cmd = (
        f"lssyscfg -r lpar -m {SYSTEM_NAME} "
        f"--filter lpar_names={LPAR_NAME} -F msp"
    )
    conn_mock.run.assert_called_once_with(expected_cmd, check=True)
    assert result is True


def test_get_lpar_msp_returns_true_when_enabled(monkeypatch):
    """hmc_get_lpar_msp returns True when the HMC reports msp=1."""
    _hmc_env(monkeypatch)
    conn_mock = _make_ssh_mock("1\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_get_lpar_msp(LPAR_NAME, SYSTEM_NAME)

    assert result is True


def test_get_lpar_msp_returns_false_when_disabled(monkeypatch):
    """hmc_get_lpar_msp returns False when the HMC reports msp=0."""
    _hmc_env(monkeypatch)
    conn_mock = _make_ssh_mock("0\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_get_lpar_msp(LPAR_NAME, SYSTEM_NAME)

    assert result is False


# ---------------------------------------------------------------------- #
# hmc_set_lpar_msp
# ---------------------------------------------------------------------- #


def test_set_lpar_msp_enabled_runs_correct_command(monkeypatch):
    """hmc_set_lpar_msp with enabled=True issues chsyscfg with msp=1."""
    _hmc_env(monkeypatch)
    conn_mock = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_set_lpar_msp(LPAR_NAME, SYSTEM_NAME, True)

    expected_cmd = (
        f'chsyscfg -r lpar -m {SYSTEM_NAME} '
        f'-i "name={LPAR_NAME},msp=1"'
    )
    conn_mock.run.assert_called_once_with(expected_cmd, check=True)
    assert result == ""


def test_set_lpar_msp_disabled_runs_correct_command(monkeypatch):
    """hmc_set_lpar_msp with enabled=False issues chsyscfg with msp=0."""
    _hmc_env(monkeypatch)
    conn_mock = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_set_lpar_msp(LPAR_NAME, SYSTEM_NAME, False)

    expected_cmd = (
        f'chsyscfg -r lpar -m {SYSTEM_NAME} '
        f'-i "name={LPAR_NAME},msp=0"'
    )
    conn_mock.run.assert_called_once_with(expected_cmd, check=True)
    assert result == ""


def test_set_lpar_msp_returns_cli_output(monkeypatch):
    """hmc_set_lpar_msp returns the raw SSH stdout verbatim."""
    _hmc_env(monkeypatch)
    RAW_OUTPUT = "0 objects successfully changed.\n"
    conn_mock = _make_ssh_mock(RAW_OUTPUT)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_set_lpar_msp(LPAR_NAME, SYSTEM_NAME, True)

    assert result == RAW_OUTPUT
