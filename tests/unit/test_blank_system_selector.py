"""A blank managed-system selector reads as absent on every path (#945, ADR 0094).

ADR 0094 already reads ``""`` and whitespace as an omitted selector on the
guarded-mutation path (``test_a_blank_system_selector_is_read_as_absent``).
These tests hold the read and resolve paths to the same rule, so ``--system ""``
and an MCP client's serialised unset optional scope every command family alike
instead of resolving a managed system named ``""``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from hmcpctl.documents import LparResources
from hmcpctl.operations.lpar.assignments import LparPcieAssignments
from hmcpctl.operations.lpar.core import get_lpar, list_lpars
from hmcpctl.operations.lpar.dlpar import modify_lpar
from hmcpctl.operations.lpar.ownership import list_lpar_ownership
from hmcpctl.operations.metrics.pcm import (
    resolve_pcm_resource,
    validate_pcm_metric_target,
)
from hmcpctl.operations.vios.core import list_vios
from hmcpctl.operations.virtualization.adapters import list_adapters
from hmcpctl.resource_identity import (
    optional_system_selector,
    resolve_lpar_uuid,
    resolve_vios_uuid,
)

BLANKS = pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
LPAR_UUID = "11111111-1111-4111-8111-111111111111"
VIOS_UUID = "33333333-3333-4333-8333-333333333333"


def _hmc() -> AsyncMock:
    hmc = AsyncMock()
    hmc.find_partition_by_name.return_value = {"UUID": LPAR_UUID}
    hmc.find_vios_by_name.return_value = {"UUID": VIOS_UUID}
    return hmc


@BLANKS
def test_the_shared_rule_reads_a_blank_selector_as_absent(blank):
    assert optional_system_selector(blank) is None
    assert optional_system_selector(None) is None


def test_the_shared_rule_keeps_a_named_selector_without_padding():
    assert optional_system_selector("  frame-1 ") == "frame-1"


@BLANKS
@pytest.mark.asyncio
async def test_lpar_name_resolution_ignores_a_blank_system(blank):
    hmc = _hmc()

    assert await resolve_lpar_uuid(hmc, "lp1", system_name_or_uuid=blank) == LPAR_UUID

    hmc.find_system_by_name.assert_not_awaited()
    hmc.find_partition_by_name.assert_awaited_once_with("lp1")


@BLANKS
@pytest.mark.asyncio
async def test_vios_name_resolution_ignores_a_blank_system(blank):
    hmc = _hmc()

    assert await resolve_vios_uuid(hmc, "vios1", system_name_or_uuid=blank) == VIOS_UUID

    hmc.find_system_by_name.assert_not_awaited()
    hmc.find_vios_by_name.assert_awaited_once_with("vios1")


@BLANKS
@pytest.mark.asyncio
async def test_adapter_listing_ignores_a_blank_system(blank):
    """``adapters list --system ""`` resolves the partition fleet-wide."""
    hmc = _hmc()
    hmc.list_adapters.return_value = []

    await list_adapters(hmc, blank, "lp1", "ClientNetworkAdapter")

    hmc.find_system_by_name.assert_not_awaited()
    hmc.list_adapters.assert_awaited_once_with(LPAR_UUID, "ClientNetworkAdapter")


@BLANKS
@pytest.mark.asyncio
async def test_lpar_and_vios_inventory_ignore_a_blank_system(blank):
    hmc = _hmc()

    await list_lpars(hmc, blank)
    await list_vios(hmc, blank)
    await get_lpar(hmc, "lp1", system_name_or_uuid=blank)

    hmc.find_system_by_name.assert_not_awaited()
    hmc.list_logical_partitions.assert_awaited_once_with(None)
    hmc.list_vios.assert_awaited_once_with(None)
    hmc.find_partition_by_name.assert_awaited_once_with("lp1", system_uuid=None)


@BLANKS
@pytest.mark.asyncio
async def test_ownership_listing_walks_the_fleet_for_a_blank_system(blank):
    hmc = _hmc()
    hmc.list_uom.return_value = []

    assert await list_lpar_ownership(hmc, blank) == []

    hmc.find_system_by_name.assert_not_awaited()
    hmc.list_uom.assert_awaited_once_with("LogicalPartition")


@BLANKS
@pytest.mark.asyncio
async def test_a_rename_with_a_blank_system_reports_the_missing_selector(blank):
    hmc = _hmc()

    with pytest.raises(ValueError, match="system_name_or_uuid is required"):
        await modify_lpar(
            hmc,
            blank,
            "lp1",
            LparResources(),
            LparPcieAssignments(),
            new_name="lp2",
        )

    hmc.find_system_by_name.assert_not_awaited()


@BLANKS
@pytest.mark.asyncio
async def test_pcm_targets_read_a_blank_system_as_absent(blank):
    hmc = _hmc()
    hmc.find_system_by_name.return_value = {"UUID": "system-uuid"}

    validate_pcm_metric_target("ManagedSystem", blank)
    target = await resolve_pcm_resource(
        hmc, "ManagedSystem", "frame-1", system_name_or_uuid=blank
    )
    assert target.resource_uuid == "system-uuid"

    with pytest.raises(ValueError, match="require the owning system_name_or_uuid"):
        validate_pcm_metric_target("LogicalPartition", blank)
    with pytest.raises(ValueError, match="require the owning system_name_or_uuid"):
        await resolve_pcm_resource(
            hmc, "LogicalPartition", "lp1", system_name_or_uuid=blank
        )
