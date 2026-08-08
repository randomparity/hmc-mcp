"""Tests for vNIC management tools (SSH CLI path)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


from hmc_mcp.server import hmc_add_vnic, hmc_list_vnics, hmc_remove_vnic

from conftest import mock_uuid_resolution

SYSTEM_UUID = "system-uuid-0001"
SYSTEM_NAME = "Server-9080-M9S-SN12345"
LPAR_UUID = "lpar-uuid-0001"
LPAR_NAME = "my-lpar"

# Sample lshwres -r virtualio --rsubtype vnic output (key=value CSV-like format)
_VNIC_LIST_OUTPUT = (
    "lpar_name=my-lpar,slot_num=2,vnic_id=1,capacity=2,vswitch_name=ETHERNET0,"
    "port_vlan_id=100,state=Open,backing_devices=\n"
    "lpar_name=my-lpar,slot_num=3,vnic_id=2,capacity=5,vswitch_name=ETHERNET0,"
    "port_vlan_id=200,state=Open,backing_devices=U78DA.001.XYZ-P1-C4-T1\n"
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


# ---------------------------------------------------------------------- #
# hmc_list_vnics
# ---------------------------------------------------------------------- #


def test_list_vnics_correct_command(monkeypatch, mock_hmc):
    """hmc_list_vnics issues the right lshwres subcommand."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock(_VNIC_LIST_OUTPUT)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        hmc_list_vnics(SYSTEM_UUID, LPAR_UUID)

    called_cmd = conn_mock.run.call_args[0][0]
    assert "lshwres" in called_cmd
    assert "--rsubtype vnic" in called_cmd
    assert "--level lpar" in called_cmd
    assert f"-m {SYSTEM_NAME}" in called_cmd
    assert f"--filter lpar_names={LPAR_NAME}" in called_cmd


def test_list_vnics_returns_list_of_dicts(monkeypatch, mock_hmc):
    """hmc_list_vnics returns a list of dicts parsed from lshwres output."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock(_VNIC_LIST_OUTPUT)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_list_vnics(SYSTEM_UUID, LPAR_UUID)

    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["lpar_name"] == "my-lpar"
    assert result[0]["vnic_id"] == "1"
    assert result[1]["vnic_id"] == "2"


def test_list_vnics_empty_output(monkeypatch, mock_hmc):
    """hmc_list_vnics returns [] when the HMC returns no output."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_list_vnics(SYSTEM_UUID, LPAR_UUID)

    assert result == []


# ---------------------------------------------------------------------- #
# hmc_add_vnic
# ---------------------------------------------------------------------- #


def test_add_vnic_correct_command_minimal(monkeypatch, mock_hmc):
    """hmc_add_vnic issues the right chhwres command with minimal params."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock("Command completed successfully.\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_add_vnic(
            system_uuid=SYSTEM_UUID,
            lpar_uuid=LPAR_UUID,
            capacity=2,
            vswitch_name="ETHERNET0",
            port_vlan_id=100,
        )

    called_cmd = conn_mock.run.call_args[0][0]
    assert "chhwres" in called_cmd
    assert "--rsubtype vnic" in called_cmd
    assert "-o a" in called_cmd
    assert f"-m {SYSTEM_NAME}" in called_cmd
    assert f"lpar_names={LPAR_NAME}" in called_cmd
    assert "capacity=2" in called_cmd
    assert "vswitch_name=ETHERNET0" in called_cmd
    assert "port_vlan_id=100" in called_cmd
    assert "completed successfully" in result


def test_add_vnic_with_backing_devices(monkeypatch, mock_hmc):
    """hmc_add_vnic includes backing_devices in the attribute string when provided."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock("Command completed successfully.\n")
    backing = "U78DA.001.XYZ-P1-C4-T1"

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        hmc_add_vnic(
            system_uuid=SYSTEM_UUID,
            lpar_uuid=LPAR_UUID,
            capacity=5,
            vswitch_name="ETHERNET0",
            port_vlan_id=200,
            backing_devices=backing,
        )

    called_cmd = conn_mock.run.call_args[0][0]
    assert f"backing_devices={backing}" in called_cmd


def test_add_vnic_without_backing_devices_excludes_it(monkeypatch, mock_hmc):
    """hmc_add_vnic omits backing_devices from the attribute string when None."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock("Command completed successfully.\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        hmc_add_vnic(
            system_uuid=SYSTEM_UUID,
            lpar_uuid=LPAR_UUID,
            capacity=2,
            vswitch_name="ETHERNET0",
            port_vlan_id=100,
        )

    called_cmd = conn_mock.run.call_args[0][0]
    assert "backing_devices" not in called_cmd


def test_add_vnic_sriov_mode_error_path(monkeypatch, mock_hmc):
    """hmc_add_vnic returns a structured error when the adapter is not in sriov mode."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)

    import asyncssh

    conn_mock = _make_ssh_mock()
    error_stderr = "HSCL3205 The adapter is not in SR-IOV mode."
    conn_mock.run = AsyncMock(
        side_effect=asyncssh.ProcessError(
            env={},
            command="chhwres",
            subsystem=None,
            exit_status=1,
            exit_signal=None,
            returncode=1,
            stdout="",
            stderr=error_stderr,
        )
    )

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_add_vnic(
            system_uuid=SYSTEM_UUID,
            lpar_uuid=LPAR_UUID,
            capacity=2,
            vswitch_name="ETHERNET0",
            port_vlan_id=100,
        )

    assert "ERROR" in result
    assert "sriov" in result.lower() or "SR-IOV" in result or "not in" in result.lower()


# ---------------------------------------------------------------------- #
# hmc_remove_vnic
# ---------------------------------------------------------------------- #


def test_remove_vnic_correct_command(monkeypatch, mock_hmc):
    """hmc_remove_vnic issues the right chhwres -o r command."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock("Command completed successfully.\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_remove_vnic(SYSTEM_UUID, LPAR_UUID, "1")

    called_cmd = conn_mock.run.call_args[0][0]
    assert "chhwres" in called_cmd
    assert "--rsubtype vnic" in called_cmd
    assert "-o r" in called_cmd
    assert f"-m {SYSTEM_NAME}" in called_cmd
    assert f"lpar_names={LPAR_NAME}" in called_cmd
    assert "vnic_id=1" in called_cmd
    assert "completed successfully" in result


def test_remove_vnic_returns_output(monkeypatch, mock_hmc):
    """hmc_remove_vnic returns the raw command output."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_remove_vnic(SYSTEM_UUID, LPAR_UUID, "2")

    assert result == ""
