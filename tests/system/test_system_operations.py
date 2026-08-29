"""Tests for presentation-neutral managed-system read operations."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from hmc_mcp.operations.systems import get_system, list_systems
from hmc_mcp.operations.lpar.core import list_lpars
from hmc_mcp.operations.vios import list_vios


@pytest.mark.asyncio
async def test_list_systems_uses_server_side_state_filter() -> None:
    hmc = AsyncMock()
    hmc.search_uom.return_value = [{"UUID": "system-1"}]

    result = await list_systems(hmc, "operating")

    hmc.search_uom.assert_awaited_once_with("ManagedSystem", "State", "operating")
    hmc.list_managed_systems.assert_not_awaited()
    assert result == [{"UUID": "system-1"}]


@pytest.mark.asyncio
async def test_get_system_routes_names_and_uuids_to_the_correct_client_method() -> None:
    hmc = AsyncMock()
    hmc.find_system_by_name.return_value = {"UUID": "system-1"}
    uuid = "12345678-1234-1234-1234-123456789abc"
    hmc.get_managed_system.return_value = {"UUID": uuid}

    assert await get_system(hmc, "frame-1") == {"UUID": "system-1"}
    assert await get_system(hmc, uuid) == {"UUID": uuid}

    hmc.find_system_by_name.assert_awaited_once_with("frame-1")
    hmc.get_managed_system.assert_awaited_once_with(uuid)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", [list_lpars, list_vios])
async def test_partition_inventory_rejects_conflicting_selectors(operation) -> None:
    hmc = AsyncMock()

    with pytest.raises(ValueError, match="at most one"):
        await operation(hmc, "system-1", "running")

    hmc.search_uom.assert_not_awaited()
