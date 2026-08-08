"""Tests for LPAR profile backup / restore / sync / I/O slot assignment tools (SSH CLI path)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hmc_mcp.config import HMCConfig
from hmc_mcp.server import (
    hmc_assign_profile_io_slot,
    hmc_backup_lpar_profiles,
    hmc_restore_lpar_profiles,
    hmc_sync_lpar_profile,
)

SYSTEM_NAME = "managed_sys1"
LPAR_NAME = "lpar1"
PROFILE_NAME = "profile1"
DRC_INDEX = "10000000"


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
# hmc_backup_lpar_profiles
# ---------------------------------------------------------------------- #


def test_backup_lpar_profiles_runs_correct_command(monkeypatch):
    """hmc_backup_lpar_profiles issues bkprofdata with correct system and file path."""
    _hmc_env(monkeypatch)
    BACKUP_OUTPUT = "Backup operation completed successfully.\n"
    conn_mock = _make_ssh_mock(BACKUP_OUTPUT)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_backup_lpar_profiles(SYSTEM_NAME, "/tmp/lpar_profiles.bak")

    expected_cmd = f"bkprofdata -m {SYSTEM_NAME} -f /tmp/lpar_profiles.bak"
    conn_mock.run.assert_called_once_with(expected_cmd, check=True)
    assert "completed successfully" in result


def test_backup_lpar_profiles_returns_cli_output(monkeypatch):
    """hmc_backup_lpar_profiles returns the raw SSH stdout verbatim."""
    _hmc_env(monkeypatch)
    RAW_OUTPUT = "Operation: backup\nStatus: OK\nFile: /tmp/profiles\n"
    conn_mock = _make_ssh_mock(RAW_OUTPUT)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_backup_lpar_profiles(SYSTEM_NAME, "/tmp/profiles")

    assert result == RAW_OUTPUT


# ---------------------------------------------------------------------- #
# hmc_restore_lpar_profiles
# ---------------------------------------------------------------------- #


def test_restore_lpar_profiles_runs_correct_command(monkeypatch):
    """hmc_restore_lpar_profiles issues rstprofdata with correct system and file path."""
    _hmc_env(monkeypatch)
    RESTORE_OUTPUT = "Restore operation completed successfully.\n"
    conn_mock = _make_ssh_mock(RESTORE_OUTPUT)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_restore_lpar_profiles(SYSTEM_NAME, "/tmp/lpar_profiles.bak")

    expected_cmd = f"rstprofdata -m {SYSTEM_NAME} -f /tmp/lpar_profiles.bak"
    conn_mock.run.assert_called_once_with(expected_cmd, check=True)
    assert "completed successfully" in result


def test_restore_lpar_profiles_returns_cli_output(monkeypatch):
    """hmc_restore_lpar_profiles returns the raw SSH stdout verbatim."""
    _hmc_env(monkeypatch)
    RAW_OUTPUT = "Operation: restore\nStatus: OK\nFile: /tmp/profiles.bak\n"
    conn_mock = _make_ssh_mock(RAW_OUTPUT)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_restore_lpar_profiles(SYSTEM_NAME, "/tmp/profiles.bak")

    assert result == RAW_OUTPUT


# ---------------------------------------------------------------------- #
# hmc_sync_lpar_profile
# ---------------------------------------------------------------------- #


def test_sync_lpar_profile_runs_correct_command(monkeypatch):
    """hmc_sync_lpar_profile issues chsyscfg with correct sync parameters."""
    _hmc_env(monkeypatch)
    SYNC_OUTPUT = "Profile sync completed successfully.\n"
    conn_mock = _make_ssh_mock(SYNC_OUTPUT)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_sync_lpar_profile(LPAR_NAME, SYSTEM_NAME)

    expected_cmd = (
        f'chsyscfg -r lpar -m {SYSTEM_NAME} -i "name={LPAR_NAME},sync_curr_profile=1"'
    )
    conn_mock.run.assert_called_once_with(expected_cmd, check=True)
    assert "successfully" in result


def test_sync_lpar_profile_returns_cli_output(monkeypatch):
    """hmc_sync_lpar_profile returns the raw SSH stdout verbatim."""
    _hmc_env(monkeypatch)
    RAW_OUTPUT = "Operation: sync\nStatus: OK\nLPAR: lpar1\n"
    conn_mock = _make_ssh_mock(RAW_OUTPUT)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_sync_lpar_profile(LPAR_NAME, SYSTEM_NAME)

    assert result == RAW_OUTPUT


# ---------------------------------------------------------------------- #
# hmc_assign_profile_io_slot
# ---------------------------------------------------------------------- #


def test_assign_profile_io_slot_runs_correct_command(monkeypatch):
    """hmc_assign_profile_io_slot issues chsyscfg with correct I/O slot parameters."""
    _hmc_env(monkeypatch)
    ASSIGN_OUTPUT = "I/O slot assignment completed successfully.\n"
    conn_mock = _make_ssh_mock(ASSIGN_OUTPUT)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_assign_profile_io_slot(SYSTEM_NAME, LPAR_NAME, PROFILE_NAME, DRC_INDEX)

    expected_cmd = (
        f'chsyscfg -r prof -m {SYSTEM_NAME} -i "name={PROFILE_NAME},io_slots+={DRC_INDEX}//0,lpar_name={LPAR_NAME}" --force'
    )
    conn_mock.run.assert_called_once_with(expected_cmd, check=True)
    assert "successfully" in result


def test_assign_profile_io_slot_returns_cli_output(monkeypatch):
    """hmc_assign_profile_io_slot returns the raw SSH stdout verbatim."""
    _hmc_env(monkeypatch)
    RAW_OUTPUT = "Operation: assign\nStatus: OK\nDRC: 10000000\n"
    conn_mock = _make_ssh_mock(RAW_OUTPUT)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_assign_profile_io_slot(SYSTEM_NAME, LPAR_NAME, PROFILE_NAME, DRC_INDEX)

    assert result == RAW_OUTPUT
