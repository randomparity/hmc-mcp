"""Tests for LPAR processor compatibility mode tools (SSH CLI path)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hmc_mcp.server import (
    hmc_get_proc_compat_modes,
    hmc_get_lpar_proc_compat,
    hmc_set_lpar_proc_compat,
)

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
# hmc_get_proc_compat_modes
# ---------------------------------------------------------------------- #


def test_get_proc_compat_modes_runs_correct_command(monkeypatch):
    """hmc_get_proc_compat_modes issues lssyscfg sys with correct arguments."""
    _hmc_env(monkeypatch)
    conn_mock = _make_ssh_mock("default,POWER8,POWER9,POWER10\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_get_proc_compat_modes(SYSTEM_NAME)

    expected_cmd = f"lssyscfg -r sys -m {SYSTEM_NAME} -F lpar_proc_compat_modes"
    conn_mock.run.assert_called_once_with(expected_cmd, check=True)
    assert result == ["default", "POWER8", "POWER9", "POWER10"]


def test_get_proc_compat_modes_returns_empty_when_none(monkeypatch):
    """hmc_get_proc_compat_modes handles empty outputs gracefully."""
    _hmc_env(monkeypatch)
    conn_mock = _make_ssh_mock("\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_get_proc_compat_modes(SYSTEM_NAME)

    assert result == []


# ---------------------------------------------------------------------- #
# hmc_get_lpar_proc_compat
# ---------------------------------------------------------------------- #


def test_get_lpar_proc_compat_runs_correct_command(monkeypatch):
    """hmc_get_lpar_proc_compat issues lssyscfg lpar with correct arguments."""
    _hmc_env(monkeypatch)
    conn_mock = _make_ssh_mock("POWER9,POWER8\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_get_lpar_proc_compat(LPAR_NAME, SYSTEM_NAME)

    expected_cmd = (
        f"lssyscfg -r lpar -m {SYSTEM_NAME} --filter lpar_names={LPAR_NAME} "
        "-F pend_lpar_proc_compat_mode,curr_lpar_proc_compat_mode"
    )
    conn_mock.run.assert_called_once_with(expected_cmd, check=True)
    assert result == {"pend": "POWER9", "curr": "POWER8"}


def test_get_lpar_proc_compat_handles_empty_output(monkeypatch):
    """hmc_get_lpar_proc_compat handles empty CLI output correctly."""
    _hmc_env(monkeypatch)
    conn_mock = _make_ssh_mock("\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_get_lpar_proc_compat(LPAR_NAME, SYSTEM_NAME)

    assert result == {"pend": "", "curr": ""}


# ---------------------------------------------------------------------- #
# hmc_set_lpar_proc_compat
# ---------------------------------------------------------------------- #


def test_set_lpar_proc_compat_runs_correct_command(monkeypatch):
    """hmc_set_lpar_proc_compat issues chsyscfg with correct arguments."""
    _hmc_env(monkeypatch)
    conn_mock = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_set_lpar_proc_compat(LPAR_NAME, SYSTEM_NAME, "POWER9")

    expected_cmd = (
        f'chsyscfg -r lpar -m {SYSTEM_NAME} '
        f'-i "name={LPAR_NAME},lpar_proc_compat_mode=POWER9"'
    )
    conn_mock.run.assert_called_once_with(expected_cmd, check=True)
    assert result == ""
