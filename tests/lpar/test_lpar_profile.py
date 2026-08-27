"""Tests for LPAR profile backup / restore / sync / I/O slot assignment tools (SSH CLI path)."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


from hmc_mcp.server import (
    hmc_backup_lpar_profiles,
    hmc_restore_lpar_profiles,
    hmc_sync_lpar_profile,
)

from conftest import mock_uuid_resolution

SYSTEM_UUID = "22222222-2222-4222-8222-222222222222"
SYSTEM_NAME = "managed_sys1"
LPAR_UUID = "11111111-1111-4111-8111-111111111111"
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


def test_backup_lpar_profiles_runs_correct_command(monkeypatch, mock_hmc):
    """hmc_backup_lpar_profiles issues bkprofdata with correct system and file path."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    BACKUP_OUTPUT = "Backup operation completed successfully.\n"
    conn_mock = _make_ssh_mock(BACKUP_OUTPUT)

    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=conn_mock):
        result = hmc_backup_lpar_profiles(SYSTEM_UUID, "/tmp/lpar_profiles.bak")

    expected_cmd = f"bkprofdata -m {SYSTEM_NAME} -f /tmp/lpar_profiles.bak"
    conn_mock.run.assert_awaited_with(expected_cmd, check=True, timeout=300.0)
    assert "completed successfully" in result


def test_backup_lpar_profiles_returns_cli_output(monkeypatch, mock_hmc):
    """hmc_backup_lpar_profiles returns the raw SSH stdout verbatim."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    RAW_OUTPUT = "Operation: backup\nStatus: OK\nFile: /tmp/profiles\n"
    conn_mock = _make_ssh_mock(RAW_OUTPUT)

    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=conn_mock):
        result = hmc_backup_lpar_profiles(SYSTEM_UUID, "/tmp/profiles")

    assert result == RAW_OUTPUT


def test_backup_lpar_profiles_force_flag_appended(monkeypatch, mock_hmc):
    """hmc_backup_lpar_profiles with force=True appends --force to bkprofdata."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    BACKUP_OUTPUT = "Backup operation completed successfully.\n"
    conn_mock = _make_ssh_mock(BACKUP_OUTPUT)

    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=conn_mock):
        result = hmc_backup_lpar_profiles(SYSTEM_UUID, "/tmp/lpar_profiles.bak", force=True)

    expected_cmd = f"bkprofdata -m {SYSTEM_NAME} -f /tmp/lpar_profiles.bak --force"
    conn_mock.run.assert_called_once_with(expected_cmd, check=True, timeout=300.0)
    assert "completed successfully" in result


def test_backup_lpar_profiles_no_force_by_default(monkeypatch, mock_hmc):
    """hmc_backup_lpar_profiles default force=False does not add --force to bkprofdata."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    conn_mock = _make_ssh_mock("OK\n")

    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=conn_mock):
        hmc_backup_lpar_profiles(SYSTEM_UUID, "/tmp/profiles")

    called_cmd = conn_mock.run.call_args[0][0]
    assert "--force" not in called_cmd


def test_backup_lpar_profiles_empty_file_path_raises(monkeypatch, mock_hmc):
    """hmc_backup_lpar_profiles raises ValueError for empty file_path."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)

    with pytest.raises(ValueError, match="file_path must not be empty"):
        hmc_backup_lpar_profiles(SYSTEM_UUID, "")


def test_backup_lpar_profiles_whitespace_file_path_raises(monkeypatch, mock_hmc):
    """hmc_backup_lpar_profiles raises ValueError for whitespace-only file_path."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)

    with pytest.raises(ValueError, match="file_path must not be empty"):
        hmc_backup_lpar_profiles(SYSTEM_UUID, "   ")


# ---------------------------------------------------------------------- #
# hmc_restore_lpar_profiles
# ---------------------------------------------------------------------- #


def test_restore_lpar_profiles_runs_correct_command(monkeypatch, mock_hmc):
    """hmc_restore_lpar_profiles issues rstprofdata with correct system and file path."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    RESTORE_OUTPUT = "Restore operation completed successfully.\n"
    conn_mock = _make_ssh_mock(RESTORE_OUTPUT)

    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=conn_mock):
        result = hmc_restore_lpar_profiles(SYSTEM_UUID, "/tmp/lpar_profiles.bak")

    expected_cmd = f"rstprofdata -m {SYSTEM_NAME} -f /tmp/lpar_profiles.bak"
    conn_mock.run.assert_called_once_with(expected_cmd, check=True, timeout=300.0)
    assert "completed successfully" in result


def test_restore_lpar_profiles_returns_cli_output(monkeypatch, mock_hmc):
    """hmc_restore_lpar_profiles returns the raw SSH stdout verbatim."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    RAW_OUTPUT = "Operation: restore\nStatus: OK\nFile: /tmp/profiles.bak\n"
    conn_mock = _make_ssh_mock(RAW_OUTPUT)

    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=conn_mock):
        result = hmc_restore_lpar_profiles(SYSTEM_UUID, "/tmp/profiles.bak")

    assert result == RAW_OUTPUT


# ---------------------------------------------------------------------- #
# hmc_sync_lpar_profile
# ---------------------------------------------------------------------- #


def test_sync_lpar_profile_runs_correct_command(monkeypatch, mock_hmc):
    """hmc_sync_lpar_profile issues chsyscfg with correct sync parameters."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    SYNC_OUTPUT = "Profile sync completed successfully.\n"
    conn_mock = _make_ssh_mock(SYNC_OUTPUT)

    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=conn_mock):
        result = hmc_sync_lpar_profile(SYSTEM_UUID, LPAR_UUID)

    expected_cmd = (
        f"chsyscfg -r lpar -m {SYSTEM_NAME} -i name={LPAR_NAME},sync_curr_profile=1"
    )
    conn_mock.run.assert_awaited_with(expected_cmd, check=True, timeout=300.0)
    assert "successfully" in result


def test_sync_lpar_profile_returns_cli_output(monkeypatch, mock_hmc):
    """hmc_sync_lpar_profile returns the raw SSH stdout verbatim."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    RAW_OUTPUT = "Operation: sync\nStatus: OK\nLPAR: lpar1\n"
    conn_mock = _make_ssh_mock(RAW_OUTPUT)

    with patch("hmc_mcp.ssh.transport.asyncssh.connect", return_value=conn_mock):
        result = hmc_sync_lpar_profile(SYSTEM_UUID, LPAR_UUID)

    assert result == RAW_OUTPUT
