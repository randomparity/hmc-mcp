"""Tests for FC port and SEA adapter listing tools (SSH CLI path)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


from hmc_mcp.server import hmc_list_fc_ports, hmc_list_sea_adapters

from conftest import mock_uuid_resolution

SYSTEM_UUID = "22222222-2222-4222-8222-222222222222"
SYSTEM_NAME = "Server-9009-42A-SN12345"
LPAR_UUID = "11111111-1111-4111-8111-111111111111"
LPAR_NAME = "my-lpar"

FC_CSV_OUTPUT = (
    "lpar_name,slot_num,wwpns,remote_lpar_id,remote_slot_num\n"
    "my-lpar,2,C050760E2B4C0001,0,0\n"
    "other-lpar,3,C050760E2B4C0002,0,0\n"
)

SEA_LINE_OUTPUT = (
    "my-lpar,1000,ETHERNET0,Open,1\n"
    "other-lpar,2000,ETHERNET0,Open,1\n"
)


def _make_ssh_mock(stdout: str = "") -> MagicMock:
    result = MagicMock()
    result.stdout = stdout
    conn = AsyncMock()
    conn.run = AsyncMock(return_value=result)
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    return conn


def _hmc_env(monkeypatch):
    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "abc123")


# ---------------------------------------------------------------------- #
# hmc_list_fc_ports
# ---------------------------------------------------------------------- #


def test_list_fc_ports_returns_list(monkeypatch, mock_hmc):
    """hmc_list_fc_ports returns a list of dicts parsed from lshwres CSV output."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    conn_mock = _make_ssh_mock(FC_CSV_OUTPUT)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_list_fc_ports(SYSTEM_UUID)

    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["lpar_name"] == "my-lpar"
    assert result[0]["wwpns"] == "C050760E2B4C0001"


def test_list_fc_ports_filter_by_lpar(monkeypatch, mock_hmc):
    """hmc_list_fc_ports appends --filter lpar_names= when lpar_uuid is given."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock(
        "lpar_name,slot_num,wwpns,remote_lpar_id,remote_slot_num\n"
        "my-lpar,2,C050760E2B4C0001,0,0\n"
    )

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_list_fc_ports(SYSTEM_UUID, lpar_name_or_uuid=LPAR_UUID)

    called_cmd = conn_mock.run.call_args[0][0]
    assert f"--filter lpar_names={LPAR_NAME}" in called_cmd
    assert len(result) == 1


def test_list_fc_ports_empty_output(monkeypatch, mock_hmc):
    """hmc_list_fc_ports returns [] when the HMC returns no output."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    conn_mock = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_list_fc_ports(SYSTEM_UUID)

    assert result == []


def test_list_fc_ports_correct_command(monkeypatch, mock_hmc):
    """hmc_list_fc_ports issues the right lshwres subcommand."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    conn_mock = _make_ssh_mock(FC_CSV_OUTPUT)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        hmc_list_fc_ports(SYSTEM_UUID)

    called_cmd = conn_mock.run.call_args[0][0]
    assert "lshwres" in called_cmd
    assert "--rsubtype fc" in called_cmd
    assert f"-m {SYSTEM_NAME}" in called_cmd


# ---------------------------------------------------------------------- #
# hmc_list_sea_adapters
# ---------------------------------------------------------------------- #


def test_list_sea_adapters_returns_list(monkeypatch, mock_hmc):
    """hmc_list_sea_adapters returns a list of dicts with the five SEA fields."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    conn_mock = _make_ssh_mock(SEA_LINE_OUTPUT)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_list_sea_adapters(SYSTEM_UUID)

    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["lpar_name"] == "my-lpar"
    assert result[0]["port_vlan_id"] == "1000"
    assert result[0]["vswitch"] == "ETHERNET0"
    assert result[0]["state"] == "Open"
    assert result[0]["trunk_priority"] == "1"


def test_list_sea_adapters_filter_by_lpar(monkeypatch, mock_hmc):
    """hmc_list_sea_adapters appends --filter lpar_names= when lpar_uuid is given."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock("my-lpar,1000,ETHERNET0,Open,1\n")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_list_sea_adapters(SYSTEM_UUID, lpar_name_or_uuid=LPAR_UUID)

    called_cmd = conn_mock.run.call_args[0][0]
    assert f"--filter lpar_names={LPAR_NAME}" in called_cmd
    assert len(result) == 1


def test_list_sea_adapters_empty_output(monkeypatch, mock_hmc):
    """hmc_list_sea_adapters returns [] when the HMC returns no output."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    conn_mock = _make_ssh_mock("")

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        result = hmc_list_sea_adapters(SYSTEM_UUID)

    assert result == []


def test_list_sea_adapters_correct_command(monkeypatch, mock_hmc):
    """hmc_list_sea_adapters issues the right lshwres subcommand with -F fields."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    conn_mock = _make_ssh_mock(SEA_LINE_OUTPUT)

    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn_mock):
        hmc_list_sea_adapters(SYSTEM_UUID)

    called_cmd = conn_mock.run.call_args[0][0]
    assert "lshwres" in called_cmd
    assert "--rsubtype eth" in called_cmd
    assert f"-m {SYSTEM_NAME}" in called_cmd
    assert "-F lpar_name,port_vlan_id,vswitch,state,trunk_priority" in called_cmd
