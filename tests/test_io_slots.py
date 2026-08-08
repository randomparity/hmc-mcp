"""Tests for physical I/O slot listing via SSH (hmc_list_io_slots)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hmc_mcp.config import HMCConfig
from hmc_mcp.ssh import list_io_slots


def make_config(**kw) -> HMCConfig:
    return HMCConfig(host="hmc.test", user="hscroot", password="abc123", **kw)


IO_SLOT_OUTPUT = (
    "drc_name=U78DA.ND1.ABC1234-P1-C1,pci_class=0200,feature_codes=EN0S,lpar_name=lpar1\n"
    "drc_name=U78DA.ND1.ABC1234-P1-C2,pci_class=0104,feature_codes=EJ0J,lpar_name=\n"
    "drc_name=U78DA.ND1.ABC1234-P1-C3,pci_class=0C04,feature_codes=EJ14,lpar_name=lpar2\n"
    "drc_name=U78DA.ND1.ABC1234-P1-C4,pci_class=0108,feature_codes=EN0T,lpar_name=\n"
)


def _make_ssh_mock(stdout: str = "") -> MagicMock:
    result = MagicMock()
    result.stdout = stdout
    conn = AsyncMock()
    conn.run = AsyncMock(return_value=result)
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    return conn


@pytest.mark.asyncio
async def test_list_io_slots_all_returns_list():
    """list_io_slots(adapter_type='all') returns a list of dicts from parsed output."""
    conn = _make_ssh_mock(IO_SLOT_OUTPUT)
    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn):
        slots = await list_io_slots(make_config(), "sys1")

    assert isinstance(slots, list)
    assert len(slots) == 4
    assert slots[0]["drc_name"] == "U78DA.ND1.ABC1234-P1-C1"
    assert slots[0]["pci_class"] == "0200"
    assert slots[0]["lpar_name"] == "lpar1"


@pytest.mark.asyncio
async def test_list_io_slots_command_all():
    """adapter_type='all' issues lshwres without a grep filter."""
    conn = _make_ssh_mock(IO_SLOT_OUTPUT)
    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn):
        await list_io_slots(make_config(), "sys1", adapter_type="all")

    cmd_called = conn.run.call_args[0][0]
    assert "lshwres" in cmd_called
    assert "--rsubtype slot" in cmd_called
    assert "-m sys1" in cmd_called
    assert "grep" not in cmd_called


@pytest.mark.asyncio
async def test_list_io_slots_eth_filter():
    """adapter_type='eth' appends a pci_class=0200 grep."""
    eth_output = "drc_name=U78DA.ND1.ABC1234-P1-C1,pci_class=0200,feature_codes=EN0S,lpar_name=lpar1\n"
    conn = _make_ssh_mock(eth_output)
    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn):
        slots = await list_io_slots(make_config(), "sys1", adapter_type="eth")

    cmd_called = conn.run.call_args[0][0]
    assert "pci_class=0200" in cmd_called
    assert len(slots) == 1
    assert slots[0]["pci_class"] == "0200"


@pytest.mark.asyncio
async def test_list_io_slots_sas_filter():
    """adapter_type='sas' appends a pci_class=0104 grep."""
    conn = _make_ssh_mock("")
    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn):
        await list_io_slots(make_config(), "sys1", adapter_type="sas")

    cmd_called = conn.run.call_args[0][0]
    assert "pci_class=0104" in cmd_called


@pytest.mark.asyncio
async def test_list_io_slots_san_filter():
    """adapter_type='san' appends a pci_class=0C04 grep."""
    conn = _make_ssh_mock("")
    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn):
        await list_io_slots(make_config(), "sys1", adapter_type="san")

    cmd_called = conn.run.call_args[0][0]
    assert "pci_class=0C04" in cmd_called


@pytest.mark.asyncio
async def test_list_io_slots_nvme_filter():
    """adapter_type='nvme' appends a pci_class=0108 grep."""
    conn = _make_ssh_mock("")
    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn):
        await list_io_slots(make_config(), "sys1", adapter_type="nvme")

    cmd_called = conn.run.call_args[0][0]
    assert "pci_class=0108" in cmd_called


@pytest.mark.asyncio
async def test_list_io_slots_empty_output():
    """Empty output returns an empty list (no errors)."""
    conn = _make_ssh_mock("")
    with patch("hmc_mcp.ssh.asyncssh.connect", return_value=conn):
        slots = await list_io_slots(make_config(), "sys1")

    assert slots == []


@pytest.mark.asyncio
async def test_list_io_slots_invalid_adapter_type():
    """Unknown adapter_type raises ValueError before SSH is attempted."""
    with pytest.raises(ValueError, match="adapter_type"):
        await list_io_slots(make_config(), "sys1", adapter_type="bogus")
