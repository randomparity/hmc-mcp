from unittest.mock import AsyncMock

import pytest

from hmc_mcp.common import resolve_lpar_uuid, resolve_vios_uuid


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resolver", "finder_name", "resource_name", "resource_uuid"),
    [
        (resolve_lpar_uuid, "find_partition_by_name", "aix1", "lpar-uuid"),
        (resolve_vios_uuid, "find_vios_by_name", "vios1", "vios-uuid"),
    ],
)
async def test_partition_resolver_forwards_resolved_system_scope(
    resolver, finder_name, resource_name, resource_uuid
):
    hmc = AsyncMock()
    hmc.find_system_by_name.return_value = {"UUID": "system-uuid"}
    getattr(hmc, finder_name).return_value = {"UUID": resource_uuid}

    assert await resolver(
        hmc, resource_name, system_name_or_uuid="system-name"
    ) == resource_uuid

    hmc.find_system_by_name.assert_awaited_once_with("system-name")
    getattr(hmc, finder_name).assert_awaited_once_with(
        resource_name, system_uuid="system-uuid"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("resolver", [resolve_lpar_uuid, resolve_vios_uuid])
async def test_partition_resolver_uuid_pass_through_ignores_system_scope(resolver):
    hmc = AsyncMock()
    resource_uuid = "11111111-1111-1111-1111-111111111111"

    assert await resolver(
        hmc, resource_uuid, system_name_or_uuid="system-name"
    ) == resource_uuid

    hmc.find_system_by_name.assert_not_awaited()
    hmc.find_partition_by_name.assert_not_awaited()
    hmc.find_vios_by_name.assert_not_awaited()


@pytest.mark.asyncio
async def test_lpar_resolver_preserves_no_match_guidance():
    hmc = AsyncMock()
    hmc.find_partition_by_name.return_value = None

    with pytest.raises(
        ValueError,
        match="No LPAR named 'missing' found. Use hmc_list_lpars to list available partitions.",
    ):
        await resolve_lpar_uuid(hmc, "missing")
