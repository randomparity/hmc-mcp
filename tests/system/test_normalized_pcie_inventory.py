"""Behavior tests for normalized system-scoped PCIe inventories."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from hmc_mcp.config import HMCConfig
from hmc_mcp.operations_pcie import (
    list_dedicated_slots,
    list_sriov_adapters,
    list_sriov_logical_ports,
    list_sriov_physical_ports,
)
from hmc_mcp.ssh_commands import list_dedicated_pcie_slot_rows


def _config() -> HMCConfig:
    return HMCConfig(
        host="hmc",
        user="user",
        password="password",  # pragma: allowlist secret
        _env_file=None,
    )


@pytest.mark.asyncio
async def test_dedicated_slot_reader_uses_the_admitted_projection() -> None:
    output = "drc_index,description,lpar_name\n21010003,PCIe slot,lpar1\n"
    with patch(
        "hmc_mcp.ssh_commands.run_hmc_command", AsyncMock(return_value=output)
    ) as run:
        rows = await list_dedicated_pcie_slot_rows(_config(), "sys one")

    run.assert_awaited_once_with(
        _config(),
        "lshwres -r io --rsubtype slot -m 'sys one' "
        "-F drc_index,description,lpar_name --header",
    )
    assert rows == [
        {"drc_index": "21010003", "description": "PCIe slot", "lpar_name": "lpar1"}
    ]


@pytest.mark.asyncio
async def test_dedicated_slot_reader_accepts_header_only_output() -> None:
    with patch(
        "hmc_mcp.ssh_commands.run_hmc_command",
        AsyncMock(return_value="drc_index,description,lpar_name\n"),
    ):
        assert await list_dedicated_pcie_slot_rows(_config(), "sys1") == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output",
    [
        "wrong,description,lpar_name\n1,slot,lpar1\n",
        "drc_index,description,lpar_name\n1,slot\n",
    ],
)
async def test_dedicated_slot_reader_rejects_schema_drift(output: str) -> None:
    with patch("hmc_mcp.ssh_commands.run_hmc_command", AsyncMock(return_value=output)):
        with pytest.raises(ValueError, match="header|columns"):
            await list_dedicated_pcie_slot_rows(_config(), "sys1")


@pytest.mark.asyncio
async def test_dedicated_inventory_normalizes_identity_owner_and_unknowns() -> None:
    rows = [
        {"drc_index": "21010003", "description": "PCIe slot", "lpar_name": "lpar1"},
        {"drc_index": "21010004", "description": "", "lpar_name": ""},
    ]
    with (
        patch(
            "hmc_mcp.operations_pcie.resolve_ssh_names",
            AsyncMock(return_value=("sys1", None)),
        ),
        patch(
            "hmc_mcp.operations_pcie.list_dedicated_pcie_slot_rows",
            AsyncMock(return_value=rows),
        ),
    ):
        result = await list_dedicated_slots(_config(), "system-uuid")

    assert result.capability == "available"
    assert result.unavailable_reason is None
    assert result.system == "sys1"
    assert result.items[0].drc_index == "21010003"
    assert result.items[0].owner_lpar == "lpar1"
    assert result.items[0].availability is None
    assert result.items[1].description is None
    assert result.items[1].owner_lpar is None
    assert result.items[1].availability is None


@pytest.mark.asyncio
async def test_dedicated_inventory_rejects_blank_identity() -> None:
    rows = [{"drc_index": "", "description": "slot", "lpar_name": ""}]
    with (
        patch(
            "hmc_mcp.operations_pcie.resolve_ssh_names",
            AsyncMock(return_value=("sys1", None)),
        ),
        patch(
            "hmc_mcp.operations_pcie.list_dedicated_pcie_slot_rows",
            AsyncMock(return_value=rows),
        ),
    ):
        with pytest.raises(ValueError, match="drc_index"):
            await list_dedicated_slots(_config(), "sys1")


@pytest.mark.asyncio
async def test_dedicated_inventory_rejects_whitespace_identity_and_normalizes_optionals() -> (
    None
):
    rows = [{"drc_index": "   ", "description": " ", "lpar_name": "\t"}]
    with (
        patch(
            "hmc_mcp.operations_pcie.resolve_ssh_names",
            AsyncMock(return_value=("sys1", None)),
        ),
        patch(
            "hmc_mcp.operations_pcie.list_dedicated_pcie_slot_rows",
            AsyncMock(return_value=rows),
        ),
    ):
        with pytest.raises(ValueError, match="drc_index"):
            await list_dedicated_slots(_config(), "sys1")


@pytest.mark.asyncio
async def test_dedicated_inventory_normalizes_whitespace_optional_fields() -> None:
    rows = [{"drc_index": "21010004", "description": " ", "lpar_name": "\t"}]
    with (
        patch(
            "hmc_mcp.operations_pcie.resolve_ssh_names",
            AsyncMock(return_value=("sys1", None)),
        ),
        patch(
            "hmc_mcp.operations_pcie.list_dedicated_pcie_slot_rows",
            AsyncMock(return_value=rows),
        ),
    ):
        result = await list_dedicated_slots(_config(), "sys1")

    assert result.items[0].description is None
    assert result.items[0].owner_lpar is None


@pytest.mark.asyncio
async def test_sriov_inventories_use_admitted_read_projections() -> None:
    resolver = AsyncMock(return_value=("sys1", None))
    adapter_rows = [
        {
            "adapter_id": "a1",
            "config_state": "sriov",
            "functional_state": "1",
            "phys_loc": "U1",
        }
    ]
    physical_rows = [
        {
            "adapter_id": "a1",
            "phys_port_id": "p2",
            "state": "1",
            "phys_port_loc": "U1-T2",
            "min_capacity": "1.0",
        }
    ]
    logical_rows = [
        {
            "adapter_id": "a1",
            "phys_port_id": "p2",
            "logical_port_id": "l5",
            "functional_state": "1",
            "lpar_name": "lpar",
            "lpar_id": "2",
            "capacity": "2.0",
            "max_capacity": "100.0",
        }
    ]
    with (
        patch("hmc_mcp.operations_pcie.resolve_ssh_names", resolver),
        patch(
            "hmc_mcp.operations_pcie.read_sriov_environment",
            AsyncMock(return_value=("V10R3 M1060", "8375-42A")),
        ),
        patch(
            "hmc_mcp.operations_pcie.list_sriov_adapter_rows",
            AsyncMock(return_value=adapter_rows),
        ),
        patch(
            "hmc_mcp.operations_pcie.list_sriov_physical_port_rows",
            AsyncMock(return_value=physical_rows),
        ),
        patch(
            "hmc_mcp.operations_pcie.list_sriov_configured_logical_port_rows",
            AsyncMock(return_value=logical_rows),
        ),
        patch(
            "hmc_mcp.operations_pcie.list_sriov_unconfigured_logical_port_rows",
            AsyncMock(
                return_value=[
                    {
                        "adapter_id": "a1",
                        "logical_port_id": "l3",
                        "location_code": "U1-T2-S3",
                    },
                    {
                        "adapter_id": "a1",
                        "logical_port_id": "l4",
                        "location_code": "U1-T2-S4",
                    },
                ]
            ),
        ),
    ):
        adapter = await list_sriov_adapters(_config(), "system-uuid", "a1")
        physical = await list_sriov_physical_ports(_config(), "system-uuid", "a1", "p2")
        logical = await list_sriov_logical_ports(
            _config(), "system-uuid", "a1", "p2", "l3"
        )

    for result in (adapter, physical, logical):
        assert result.capability == "available"
        assert len(result.items) == 1
        assert result.unavailable_reason is None
    assert adapter.selector.adapter_id == "a1"
    assert physical.selector.physical_port_id == "p2"
    assert logical.selector.logical_port_id == "l3"


@pytest.mark.asyncio
async def test_unconfigured_logical_port_requires_unique_physical_parent() -> None:
    with (
        patch(
            "hmc_mcp.operations_pcie.resolve_ssh_names",
            AsyncMock(return_value=("sys1", None)),
        ),
        patch(
            "hmc_mcp.operations_pcie.read_sriov_environment",
            AsyncMock(return_value=("V10R3 M1060", "8375-42A")),
        ),
        patch(
            "hmc_mcp.operations_pcie.list_sriov_configured_logical_port_rows",
            AsyncMock(return_value=[]),
        ),
        patch(
            "hmc_mcp.operations_pcie.list_sriov_unconfigured_logical_port_rows",
            AsyncMock(
                return_value=[
                    {
                        "adapter_id": "a1",
                        "logical_port_id": "l3",
                        "location_code": "unknown",
                    }
                ]
            ),
        ),
        patch(
            "hmc_mcp.operations_pcie.list_sriov_physical_port_rows",
            AsyncMock(return_value=[]),
        ),
        pytest.raises(RuntimeError, match="ambiguous physical-port parent"),
    ):
        await list_sriov_logical_ports(_config(), "system-uuid", "a1")
