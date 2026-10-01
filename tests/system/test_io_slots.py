"""Tests for physical I/O slot listing via SSH (hmc_list_io_slots)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from conftest import live_fixture, make_config

from hmcpctl.ssh.io_inventory import list_io_slots

# The default (no -F) slot listing as V10R3 prints it: an unowned slot carries
# `lpar_id=none` and no `lpar_name` pair at all, and pci_class is four upper-case
# hex digits (#1202).
IO_SLOT_CAPTURE = live_fixture("cli-io-slots-default")
IO_SLOT_OUTPUT = IO_SLOT_CAPTURE["stdout"]


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
    """list_io_slots(pci_class='all') returns one dict per captured slot line."""
    conn = _make_ssh_mock(IO_SLOT_OUTPUT)
    with patch("hmcpctl.ssh.transport.asyncssh.connect", return_value=conn):
        slots = await list_io_slots(make_config(), "sys-R1")

    assert conn.run.call_args[0][0] == IO_SLOT_CAPTURE["command"]
    assert len(slots) == 15
    by_drc = {slot["drc_index"]: slot for slot in slots}
    owned, unowned = by_drc["21020013"], by_drc["21010010"]
    assert (owned["lpar_name"], owned["lpar_id"], owned["pci_class"]) == (
        "sys-R1-vios1",
        "100",
        "0200",
    )
    # A list value comes quoted, `"feature_codes=5260,5899"`, and stays one pair.
    assert owned["feature_codes"] == "5260,5899"
    assert "lpar_name" not in unowned
    assert unowned["lpar_id"] == "none"
    assert {slot["pci_class"] for slot in slots} == {"FFFF", "0200", "0104", "0C03"}


@pytest.mark.asyncio
async def test_list_io_slots_command_all():
    """pci_class='all' issues lshwres without a grep filter."""
    conn = _make_ssh_mock(IO_SLOT_OUTPUT)
    with patch("hmcpctl.ssh.transport.asyncssh.connect", return_value=conn):
        await list_io_slots(make_config(), "sys1", pci_class="all")

    cmd_called = conn.run.call_args[0][0]
    assert "lshwres" in cmd_called
    assert "--rsubtype slot" in cmd_called
    assert "-m sys1" in cmd_called
    assert "grep" not in cmd_called


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pci_class", "drc_indexes"),
    [
        ("eth", ["21020013", "21010020", "21010021", "21010022"]),
        ("sas", ["21040015"]),
        # Neither class is in the capture: the filter yields no rows rather than
        # a failed command (`grep` exits 1 when nothing matches).
        ("san", []),
        ("nvme", []),
    ],
)
async def test_list_io_slots_filters_by_pci_class(pci_class, drc_indexes):
    """A pci_class filter selects the captured slots of that class, client-side."""
    conn = _make_ssh_mock(IO_SLOT_OUTPUT)
    with patch("hmcpctl.ssh.transport.asyncssh.connect", return_value=conn):
        slots = await list_io_slots(make_config(), "sys-R1", pci_class=pci_class)

    assert conn.run.call_args[0][0] == IO_SLOT_CAPTURE["command"]
    assert [slot["drc_index"] for slot in slots] == drc_indexes


@pytest.mark.asyncio
async def test_list_io_slots_empty_output():
    """Empty output returns an empty list (no errors)."""
    conn = _make_ssh_mock("")
    with patch("hmcpctl.ssh.transport.asyncssh.connect", return_value=conn):
        slots = await list_io_slots(make_config(), "sys1")

    assert slots == []


@pytest.mark.asyncio
async def test_list_io_slots_invalid_pci_class():
    """Unknown pci_class raises ValueError before SSH is attempted."""
    with pytest.raises(ValueError, match="pci_class"):
        await list_io_slots(make_config(), "sys1", pci_class="bogus")
