"""Tests for VIOS backup / restore / list tools (SSH CLI path)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hmc_mcp.server import hmc_backup_vios, hmc_list_vios_backups, hmc_restore_vios

VIOS_UUID = "00000000-0000-0000-0000-000000000003"


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
# hmc_list_vios_backups
# ---------------------------------------------------------------------- #


def test_list_vios_backups_runs_correct_command(monkeypatch):
    """hmc_list_vios_backups issues lsviosbackup with the correct vios_uuid."""
    _hmc_env(monkeypatch)
    LIST_OUTPUT = (
        "BackupName         Date        Type\n"
        "vios1_backup_001   2024-01-15  vios\n"
        "vios1_backup_002   2024-01-20  viosioconfig\n"
    )
    conn_mock = _make_ssh_mock(LIST_OUTPUT)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_list_vios_backups(VIOS_UUID)

    conn_mock.run.assert_called_once_with(
        f"lsviosbackup -id {VIOS_UUID}", check=True, timeout=300.0
    )
    assert result == [
        {"BackupName": "vios1_backup_001", "Date": "2024-01-15", "Type": "vios"},
        {
            "BackupName": "vios1_backup_002",
            "Date": "2024-01-20",
            "Type": "viosioconfig",
        },
    ]


def test_list_vios_backups_returns_empty_list(monkeypatch):
    """hmc_list_vios_backups returns an empty list when there are no backups."""
    _hmc_env(monkeypatch)
    conn_mock = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_list_vios_backups(VIOS_UUID)

    assert result == []


def test_list_vios_backups_resolves_vios_name(monkeypatch):
    """Backup commands resolve VIOS names before invoking the HMC CLI."""
    _hmc_env(monkeypatch)
    hmc = AsyncMock()
    hmc.find_vios_by_name.return_value = {"UUID": VIOS_UUID}

    @asynccontextmanager
    async def fake_client_from_env(profile):
        assert profile is None
        yield hmc

    monkeypatch.setattr("hmc_mcp.server_vios.client_from_env", fake_client_from_env)
    conn_mock = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        hmc_list_vios_backups("vios-prod")

    hmc.find_vios_by_name.assert_awaited_once_with("vios-prod")
    conn_mock.run.assert_awaited_once_with(
        f"lsviosbackup -id {VIOS_UUID}", check=True, timeout=300.0
    )


# ---------------------------------------------------------------------- #
# hmc_backup_vios
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize("backup_type", ["vios", "viosioconfig", "ssp"])
def test_backup_vios_valid_types(monkeypatch, backup_type):
    """hmc_backup_vios accepts each valid backup_type and passes it to chviosbackup."""
    _hmc_env(monkeypatch)
    conn_mock = _make_ssh_mock("Backup completed successfully.\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_backup_vios(VIOS_UUID, backup_type)

    expected_cmd = f"chviosbackup -id {VIOS_UUID} -operation backup -type {backup_type}"
    conn_mock.run.assert_called_once_with(expected_cmd, check=True, timeout=300.0)
    assert "Backup completed" in result


def test_backup_vios_default_type_is_vios(monkeypatch):
    """hmc_backup_vios defaults to backup_type='vios'."""
    _hmc_env(monkeypatch)
    conn_mock = _make_ssh_mock("Done.\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        hmc_backup_vios(VIOS_UUID)

    called_cmd = conn_mock.run.call_args[0][0]
    assert "-type vios" in called_cmd


def test_backup_vios_invalid_type_raises(monkeypatch):
    """hmc_backup_vios raises ValueError for unknown backup_type."""
    _hmc_env(monkeypatch)
    with pytest.raises(ValueError, match="Invalid backup_type"):
        hmc_backup_vios(VIOS_UUID, "bogus")


# ---------------------------------------------------------------------- #
# hmc_restore_vios
# ---------------------------------------------------------------------- #


def test_restore_vios_runs_correct_command(monkeypatch):
    """hmc_restore_vios issues chviosbackup restore with vios_uuid and backup_name."""
    _hmc_env(monkeypatch)
    BACKUP_NAME = "vios1_backup_001"
    conn_mock = _make_ssh_mock("Restore completed successfully.\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_restore_vios(VIOS_UUID, BACKUP_NAME)

    expected_cmd = (
        f"chviosbackup -id {VIOS_UUID} -operation restore -file {BACKUP_NAME}"
    )
    conn_mock.run.assert_called_once_with(expected_cmd, check=True, timeout=300.0)
    assert "Restore completed" in result


@pytest.mark.parametrize(
    "backup_name",
    [
        "",
        "   ",
        "../other/x.tar",
        "/data/viosbackup/x.tar",
        "a\\b.tar",
        ".",
        "..",
        " .. ",
        " backup.tar ",
        "-operation",
    ],
    ids=[
        "empty",
        "whitespace-only",
        "dot-segment",
        "absolute-path",
        "backslash",
        "single-dot",
        "double-dot",
        "padded-double-dot",
        "padded-name",
        "option-shaped",
    ],
)
def test_restore_vios_refuses_a_name_that_could_leave_the_catalog(monkeypatch, backup_name):
    """A backup_name is a name in the declared VIOS's catalog, not a path.

    ADR 0044 keeps `hmc_restore_vios` bounded because `-id` selects the catalog
    the name resolves in. That holds only while the value cannot denote anything
    outside it, so this refusal is what the classification rests on rather than an
    assumption about what the HMC does with a path-shaped `-file`.

    The option-shaped case is the one that is easy to miss: `shlex.quote` leaves
    `-operation` bare because it holds no shell metacharacter, so without this the
    value would reach the CLI as a flag rather than as a file name.
    """
    _hmc_env(monkeypatch)
    with pytest.raises(ValueError, match="backup_name"):
        hmc_restore_vios(VIOS_UUID, backup_name)


@pytest.mark.parametrize(
    "backup_name", ["vios1_backup_001", "nim_resources.tar", "cfgbackup.tar.gz", "a-b_c.1"]
)
def test_restore_vios_admits_an_ordinary_catalog_name(monkeypatch, backup_name):
    """The refusal is narrow: every shape the catalog can hold still restores."""
    _hmc_env(monkeypatch)
    conn_mock = _make_ssh_mock("Restore completed successfully.\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        hmc_restore_vios(VIOS_UUID, backup_name)

    assert f"-file {backup_name}" in conn_mock.run.call_args[0][0]


def test_restore_vios_returns_cli_output(monkeypatch):
    """hmc_restore_vios returns the raw SSH stdout verbatim."""
    _hmc_env(monkeypatch)
    RAW_OUTPUT = "Operation: restore\nStatus: OK\nFile: mybackup\n"
    conn_mock = _make_ssh_mock(RAW_OUTPUT)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_restore_vios(VIOS_UUID, "mybackup")

    assert result == RAW_OUTPUT
