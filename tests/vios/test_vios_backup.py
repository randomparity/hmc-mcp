"""Contracts for the supported VIOS backup, restore, and list commands."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hmc_mcp.server import hmc_backup_vios, hmc_list_vios_backups, hmc_restore_vios

SYSTEM_UUID = "22222222-2222-4222-8222-222222222222"
SYSTEM_NAME = "Server-9080-M9S-SN12345"
VIOS_UUID = "00000000-0000-0000-0000-000000000003"
BACKUP_NAME = "vios1_backup_001"

INVALID_BACKUP_NAMES = (
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
)


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
    """Set env vars so HMCConfig() succeeds inside the tool."""
    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "abc123")


def _client_factory(hmc):
    @asynccontextmanager
    async def factory(_profile):
        yield hmc

    return factory


@pytest.fixture(autouse=True)
def _fake_vios_client(monkeypatch):
    """Prevent command-contract tests from depending on a reachable HMC."""
    hmc = AsyncMock()
    hmc.find_system_by_name.return_value = {"UUID": SYSTEM_UUID}
    hmc.find_vios_by_name.return_value = {"UUID": VIOS_UUID}
    monkeypatch.setattr("hmc_mcp.server_vios.client_from_env", _client_factory(hmc))


# ---------------------------------------------------------------------- #
# hmc_list_vios_backups
# ---------------------------------------------------------------------- #


def test_list_vios_backups_runs_supported_command_and_parses_csv(monkeypatch):
    """Listing pins the supported projection and CSV parsing semantics."""
    _hmc_env(monkeypatch)
    output = 'name,type\r\n"nightly, ""quoted""",viosioconfig\r\nbase,ssp\r\n'
    conn_mock = _make_ssh_mock(output)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_list_vios_backups(VIOS_UUID)

    conn_mock.run.assert_called_once_with(
        f"lsviosbk --filter vios_uuids={VIOS_UUID} -F name,type --header",
        check=True,
        timeout=300.0,
    )
    assert result == [
        {"name": 'nightly, "quoted"', "type": "viosioconfig"},
        {"name": "base", "type": "ssp"},
    ]


def test_list_vios_backups_returns_empty_list(monkeypatch):
    """An empty supported-command response represents an empty backup catalog."""
    _hmc_env(monkeypatch)
    conn_mock = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_list_vios_backups(VIOS_UUID)

    assert result == []


@pytest.mark.parametrize(
    "output",
    [
        "backup,type\nbase,vios\n",
        "name,name\nbase,vios\n",
        "name,type\n,vios\n",
        "name,type\nbase,\n",
        "name,type,extra\nbase,vios,unexpected\n",
    ],
    ids=[
        "wrong-header",
        "duplicate-header",
        "empty-name",
        "empty-type",
        "extra-column",
    ],
)
def test_list_vios_backups_refuses_malformed_csv(monkeypatch, output):
    """The explicit CSV projection refuses malformed rows instead of guessing."""
    _hmc_env(monkeypatch)
    conn_mock = _make_ssh_mock(output)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        with pytest.raises(ValueError):
            hmc_list_vios_backups(VIOS_UUID)


def test_list_vios_backups_resolves_vios_name(monkeypatch):
    """A non-UUID VIOS selector resolves before the supported list command."""
    _hmc_env(monkeypatch)
    hmc = AsyncMock()
    hmc.find_vios_by_name.return_value = {"UUID": VIOS_UUID}
    monkeypatch.setattr("hmc_mcp.server_vios.client_from_env", _client_factory(hmc))
    conn_mock = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        hmc_list_vios_backups("vios-prod")

    hmc.find_vios_by_name.assert_awaited_once_with("vios-prod")
    conn_mock.run.assert_awaited_once_with(
        f"lsviosbk --filter vios_uuids={VIOS_UUID} -F name,type --header",
        check=True,
        timeout=300.0,
    )


# ---------------------------------------------------------------------- #
# hmc_backup_vios
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize("backup_type", ["vios", "viosioconfig", "ssp"])
def test_backup_vios_runs_supported_command(monkeypatch, backup_type):
    """Each supported type uses all required mkviosbk selectors and output name."""
    _hmc_env(monkeypatch)
    conn_mock = _make_ssh_mock("Backup completed successfully.\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_backup_vios(SYSTEM_NAME, VIOS_UUID, BACKUP_NAME, backup_type)

    conn_mock.run.assert_called_once_with(
        f"mkviosbk -t {backup_type} -m {SYSTEM_NAME} --uuid {VIOS_UUID} -f {BACKUP_NAME}",
        check=True,
        timeout=300.0,
    )
    assert "Backup completed" in result


def test_backup_vios_defaults_to_full_vios_type(monkeypatch):
    """The explicit output-name contract retains only the vios backup default."""
    _hmc_env(monkeypatch)
    conn_mock = _make_ssh_mock("Done.\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        hmc_backup_vios(SYSTEM_NAME, VIOS_UUID, BACKUP_NAME)

    assert "mkviosbk -t vios" in conn_mock.run.call_args.args[0]


def test_backup_vios_invalid_type_raises_before_external_calls(monkeypatch):
    """Unknown backup types cannot reach either selector resolution or SSH."""
    _hmc_env(monkeypatch)

    with patch(
        "hmc_mcp.ssh.asyncssh.connect",
        side_effect=AssertionError("reached the SSH layer"),
    ):
        with pytest.raises(ValueError, match="Invalid backup_type"):
            hmc_backup_vios(SYSTEM_NAME, VIOS_UUID, BACKUP_NAME, "bogus")


# ---------------------------------------------------------------------- #
# hmc_restore_vios
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize("backup_type", ["viosioconfig", "ssp"])
@pytest.mark.parametrize("restart_if_required", [False, True])
def test_restore_vios_runs_supported_command(
    monkeypatch, backup_type, restart_if_required
):
    """Restore uses a required supported type and emits -r only when requested."""
    _hmc_env(monkeypatch)
    conn_mock = _make_ssh_mock("Restore completed successfully.\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_restore_vios(
            SYSTEM_NAME,
            VIOS_UUID,
            BACKUP_NAME,
            backup_type,
            restart_if_required=restart_if_required,
        )

    restart_flag = " -r" if restart_if_required else ""
    conn_mock.run.assert_called_once_with(
        f"rstviosbk -t {backup_type} -m {SYSTEM_NAME} --uuid {VIOS_UUID} "
        f"-f {BACKUP_NAME}{restart_flag}",
        check=True,
        timeout=300.0,
    )
    assert "Restore completed" in result


def test_restore_vios_rejects_full_vios_type_before_external_calls(monkeypatch):
    """rstviosbk has no full-image restore mode, so vios fails closed."""
    _hmc_env(monkeypatch)

    with patch(
        "hmc_mcp.ssh.asyncssh.connect",
        side_effect=AssertionError("reached the SSH layer"),
    ):
        with pytest.raises(ValueError, match="backup_type"):
            hmc_restore_vios(SYSTEM_NAME, VIOS_UUID, BACKUP_NAME, "vios")


@pytest.mark.parametrize("backup_name", INVALID_BACKUP_NAMES)
def test_backup_vios_refuses_a_name_that_could_leave_the_catalog(
    monkeypatch, backup_name
):
    """Creation names retain the catalog-boundary validation used by restore."""
    _hmc_env(monkeypatch)

    with patch(
        "hmc_mcp.ssh.asyncssh.connect",
        side_effect=AssertionError("reached the SSH layer"),
    ):
        with pytest.raises(ValueError, match="backup_name"):
            hmc_backup_vios(SYSTEM_NAME, VIOS_UUID, backup_name)


@pytest.mark.parametrize("backup_name", INVALID_BACKUP_NAMES)
def test_restore_vios_refuses_a_name_that_could_leave_the_catalog(
    monkeypatch, backup_name
):
    """Restoration names stay catalog entries rather than paths or options."""
    _hmc_env(monkeypatch)

    with patch(
        "hmc_mcp.ssh.asyncssh.connect",
        side_effect=AssertionError("reached the SSH layer"),
    ):
        with pytest.raises(ValueError, match="backup_name"):
            hmc_restore_vios(
                SYSTEM_NAME,
                VIOS_UUID,
                backup_name,
                "ssp",
                restart_if_required=False,
            )


@pytest.mark.parametrize(
    "backup_name",
    ["vios1_backup_001", "nim_resources.tar", "cfgbackup.tar.gz", "a-b_c.1"],
)
@pytest.mark.parametrize(
    ("tool", "arguments", "keywords"),
    [
        (hmc_backup_vios, (SYSTEM_NAME, VIOS_UUID), {}),
        (
            hmc_restore_vios,
            (SYSTEM_NAME, VIOS_UUID, "ssp"),
            {"restart_if_required": False},
        ),
    ],
    ids=["backup", "restore"],
)
def test_vios_backup_tools_admit_ordinary_catalog_names(
    monkeypatch, backup_name, tool, arguments, keywords
):
    """Validation is narrow enough to retain ordinary catalog names for both tools."""
    _hmc_env(monkeypatch)
    conn_mock = _make_ssh_mock("completed\n")
    call_arguments = (*arguments[:2], backup_name, *arguments[2:])

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        tool(*call_arguments, **keywords)

    assert f"-f {backup_name}" in conn_mock.run.call_args.args[0]


def test_restore_vios_returns_cli_output(monkeypatch):
    """hmc_restore_vios returns the raw SSH stdout verbatim."""
    _hmc_env(monkeypatch)
    raw_output = "Operation: restore\nStatus: OK\nFile: mybackup\n"
    conn_mock = _make_ssh_mock(raw_output)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_restore_vios(
            SYSTEM_NAME,
            VIOS_UUID,
            "mybackup",
            "ssp",
            restart_if_required=False,
        )

    assert result == raw_output


# ---------------------------------------------------------------------- #
# Explicit system and VIOS selector resolution
# ---------------------------------------------------------------------- #


def test_backup_vios_preserves_a_direct_system_name_and_scopes_vios_name(monkeypatch):
    """A name stays the CLI identity while its VIOS lookup is system-scoped."""
    _hmc_env(monkeypatch)
    hmc = AsyncMock()
    hmc.find_system_by_name.return_value = {"UUID": SYSTEM_UUID}
    hmc.find_vios_by_name.return_value = {"UUID": VIOS_UUID}
    monkeypatch.setattr("hmc_mcp.server_vios.client_from_env", _client_factory(hmc))
    conn_mock = _make_ssh_mock("completed\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        hmc_backup_vios(SYSTEM_NAME, "vios-prod", BACKUP_NAME)

    hmc.find_vios_by_name.assert_awaited_once_with("vios-prod", system_uuid=SYSTEM_UUID)
    assert f"-m {SYSTEM_NAME} --uuid {VIOS_UUID}" in conn_mock.run.call_args.args[0]


@pytest.mark.parametrize(
    ("mtms", "expected_shell_mtms"),
    [
        ("9009-42A*1234567", "'9009-42A*1234567'"),
        (
            {"MachineType": "9009", "Model": "42A", "SerialNumber": "1234567"},
            "'9009-42A*1234567'",
        ),
    ],
    ids=["flattened", "nested"],
)
def test_backup_vios_uses_mtms_for_a_system_uuid_even_when_names_collide(
    monkeypatch, mtms, expected_shell_mtms
):
    """A UUID resolves to its unique MTMS, never an ambiguous user-defined name."""
    _hmc_env(monkeypatch)
    hmc = AsyncMock()
    hmc.get_managed_system.return_value = {
        "Resource": {
            "SystemName": SYSTEM_NAME,
            "MachineTypeModelSerialNumber": mtms,
        }
    }
    hmc.find_vios_by_name.return_value = {"UUID": VIOS_UUID}
    monkeypatch.setattr("hmc_mcp.server_vios.client_from_env", _client_factory(hmc))
    conn_mock = _make_ssh_mock("completed\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        hmc_backup_vios(SYSTEM_UUID, "vios-prod", BACKUP_NAME)

    hmc.find_vios_by_name.assert_awaited_once_with("vios-prod", system_uuid=SYSTEM_UUID)
    assert (
        f"-m {expected_shell_mtms} --uuid {VIOS_UUID}"
        in conn_mock.run.call_args.args[0]
    )
    assert SYSTEM_NAME not in conn_mock.run.call_args.args[0]


@pytest.mark.parametrize(
    "managed_system",
    [
        {"Resource": {}},
        {"Resource": {"MachineTypeModelSerialNumber": "9009-42A"}},
        {
            "Resource": {
                "MachineTypeModelSerialNumber": {
                    "MachineType": "9009",
                    "Model": "42A",
                    "SerialNumber": " ",
                }
            }
        },
    ],
    ids=["missing", "malformed-flattened", "blank-nested-component"],
)
def test_backup_vios_refuses_uuid_without_complete_mtms_before_ssh(
    monkeypatch, managed_system
):
    """A UUID without a valid CLI identity fails closed before external SSH."""
    _hmc_env(monkeypatch)
    hmc = AsyncMock()
    hmc.get_managed_system.return_value = managed_system
    monkeypatch.setattr("hmc_mcp.server_vios.client_from_env", _client_factory(hmc))

    with patch(
        "hmc_mcp.ssh.asyncssh.connect",
        side_effect=AssertionError("reached the SSH layer"),
    ):
        with pytest.raises(ValueError, match="MachineTypeModelSerialNumber|MTMS"):
            hmc_backup_vios(SYSTEM_UUID, VIOS_UUID, BACKUP_NAME)
