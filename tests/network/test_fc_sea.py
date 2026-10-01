"""Tests for FC port and SEA adapter listing tools (SSH CLI path)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from conftest import live_fixture, mock_uuid_resolution

from hmcpctl.server_tools.virtualization.vnic import (
    hmc_list_fc_ports,
    hmc_list_sea_adapters,
)

SYSTEM_UUID = "22222222-2222-4222-8222-222222222222"
SYSTEM_NAME = "Server-9009-42A-SN12345"
LPAR_UUID = "11111111-1111-4111-8111-111111111111"
LPAR_NAME = "my-lpar"

# Without -F, lshwres prints each row as name=value pairs
# (docs/refs/hmc-commands-p10/commands/lshwres.md:104), as the captured virtual
# Ethernet default listing shows. No populated vfc row is captured: these rows
# carry the chhwres vfc attribute names (chhwres.md:238) and lpar_name/slot_num
# as the virtual Ethernet listing prints them (#1202).
FC_DEFAULT_OUTPUT = (
    "lpar_name=my-lpar,lpar_id=2,slot_num=2,adapter_type=client,"
    "remote_lpar_id=1,remote_slot_num=5\n"
    "lpar_name=other-lpar,lpar_id=3,slot_num=3,adapter_type=client,"
    "remote_lpar_id=1,remote_slot_num=6\n"
)
# A system with no virtual Fibre Channel adapters, and an LPAR with no virtual
# Ethernet adapters: the read exits 0 and prints the empty-result line.
FC_EMPTY = live_fixture("cli-vio-fc-default")
SEA_EMPTY = live_fixture("cli-vio-eth-empty")
SEA_CAPTURE = live_fixture("cli-vio-eth-sea")
SEA_LINE_OUTPUT = SEA_CAPTURE["stdout"]


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
    """hmc_list_fc_ports returns one dict per default-format lshwres row."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    conn_mock = _make_ssh_mock(FC_DEFAULT_OUTPUT)

    with patch("hmcpctl.ssh.transport.asyncssh.connect", return_value=conn_mock):
        result = hmc_list_fc_ports(SYSTEM_UUID)

    assert [row["lpar_name"] for row in result] == ["my-lpar", "other-lpar"]
    assert result[0]["remote_slot_num"] == "5"
    assert result[1]["slot_num"] == "3"


def test_list_fc_ports_filter_by_lpar(monkeypatch, mock_hmc):
    """hmc_list_fc_ports appends --filter lpar_names= when lpar_uuid is given."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock(FC_DEFAULT_OUTPUT.splitlines(keepends=True)[0])

    with patch("hmcpctl.ssh.transport.asyncssh.connect", return_value=conn_mock):
        result = hmc_list_fc_ports(SYSTEM_UUID, lpar_name_or_uuid=LPAR_UUID)

    called_cmd = conn_mock.run.call_args[0][0]
    assert f"--filter lpar_names={LPAR_NAME}" in called_cmd
    assert len(result) == 1


def test_list_fc_ports_empty_output(monkeypatch, mock_hmc):
    """hmc_list_fc_ports returns [] for the HMC's empty-result line."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    conn_mock = _make_ssh_mock(FC_EMPTY["stdout"])

    with patch("hmcpctl.ssh.transport.asyncssh.connect", return_value=conn_mock):
        result = hmc_list_fc_ports(SYSTEM_UUID)

    assert result == []


def test_list_fc_ports_correct_command(monkeypatch, mock_hmc):
    """hmc_list_fc_ports issues the right lshwres subcommand."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    conn_mock = _make_ssh_mock(FC_DEFAULT_OUTPUT)

    with patch("hmcpctl.ssh.transport.asyncssh.connect", return_value=conn_mock):
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

    with patch("hmcpctl.ssh.transport.asyncssh.connect", return_value=conn_mock):
        result = hmc_list_sea_adapters(SYSTEM_UUID)

    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0] == {
        "lpar_name": "sys-R1-vios1",
        "port_vlan_id": "1",
        "vswitch": "ETHERNET0",
        "state": "1",
        "trunk_priority": "1",
    }
    assert result[1]["port_vlan_id"] == "2"


def test_list_sea_adapters_filter_by_lpar(monkeypatch, mock_hmc):
    """hmc_list_sea_adapters appends --filter lpar_names= when lpar_uuid is given."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock(SEA_LINE_OUTPUT.splitlines(keepends=True)[0])

    with patch("hmcpctl.ssh.transport.asyncssh.connect", return_value=conn_mock):
        result = hmc_list_sea_adapters(SYSTEM_UUID, lpar_name_or_uuid=LPAR_UUID)

    called_cmd = conn_mock.run.call_args[0][0]
    assert f"--filter lpar_names={LPAR_NAME}" in called_cmd
    assert len(result) == 1


def test_list_sea_adapters_empty_output(monkeypatch, mock_hmc):
    """hmc_list_sea_adapters returns [] for the HMC's empty-result line."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME, LPAR_UUID, LPAR_NAME)
    conn_mock = _make_ssh_mock(SEA_EMPTY["stdout"])

    with patch("hmcpctl.ssh.transport.asyncssh.connect", return_value=conn_mock):
        result = hmc_list_sea_adapters(SYSTEM_UUID, lpar_name_or_uuid=LPAR_UUID)

    assert result == []


def test_list_sea_adapters_correct_command(monkeypatch, mock_hmc):
    """hmc_list_sea_adapters issues the right lshwres subcommand with -F fields."""
    _hmc_env(monkeypatch)
    mock_uuid_resolution(mock_hmc, SYSTEM_UUID, SYSTEM_NAME)
    conn_mock = _make_ssh_mock(SEA_LINE_OUTPUT)

    with patch("hmcpctl.ssh.transport.asyncssh.connect", return_value=conn_mock):
        hmc_list_sea_adapters(SYSTEM_UUID)

    called_cmd = conn_mock.run.call_args[0][0]
    assert "lshwres" in called_cmd
    assert "--rsubtype eth" in called_cmd
    assert f"-m {SYSTEM_NAME}" in called_cmd
    assert "-F lpar_name,port_vlan_id,vswitch,state,trunk_priority" in called_cmd
