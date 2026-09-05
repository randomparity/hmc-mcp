"""Operation-boundary projections for VIOS storage inventory."""

from dataclasses import asdict
from typing import cast

import pytest

from hmc_mcp.client.core import HMCClient
from hmc_mcp.errors import HMCError
from hmc_mcp.operations.storage import (
    list_optical_media,
    list_storage_mappings,
    list_volume_groups,
)

VIOS_UUID = "00000000-0000-0000-0000-000000000001"


class _StorageClient:
    async def list_volume_groups(self, _vios_uuid: str):
        return [{"UUID": "vg-1", "Resource": {"GroupName": "rootvg", "FreeSpace": "10"}}]

    async def list_optical_media(self, _vios_uuid: str, _vg_uuid: str):
        return [{"MediaName": "install.iso", "MediaSize": "1024", "MediaType": "ISO"}]

    async def list_storage_mappings(self, _vios_uuid: str, _lpar_uuid=None):
        return [{"UUID": "mapping-1", "AssociatedLogicalPartition": {"href": "/rest/api/uom/LogicalPartition/lpar-1"}, "Storage": {"VirtualDisk": {"DiskName": "boot"}}}]


@pytest.mark.asyncio
async def test_storage_inventory_translates_hmc_resources() -> None:
    client = cast(HMCClient, _StorageClient())

    volume_groups = await list_volume_groups(client, VIOS_UUID)
    optical_media = await list_optical_media(client, VIOS_UUID, "vg-1")
    mappings = await list_storage_mappings(client, VIOS_UUID)

    assert asdict(volume_groups[0]) == {
        "uuid": "vg-1", "name": "rootvg", "capacity_mib": None, "free_space_mib": 10
    }
    assert asdict(optical_media[0]) == {
        "name": "install.iso", "size_mib": 1024, "media_type": "ISO"
    }
    assert asdict(mappings[0]) == {
        "uuid": "mapping-1", "lpar_uuid": "lpar-1", "backing_kind": "VirtualDisk", "backing_name": "boot"
    }


@pytest.mark.asyncio
async def test_volume_group_rejects_missing_required_name() -> None:
    class MissingName:
        async def list_volume_groups(self, _vios_uuid: str):
            return [{"UUID": "vg-1", "Resource": {}}]

    with pytest.raises(HMCError, match="GroupName"):
        await list_volume_groups(cast(HMCClient, MissingName()), VIOS_UUID)
