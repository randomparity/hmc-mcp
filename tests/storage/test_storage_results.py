"""Operation-boundary projections for VIOS storage inventory."""

from dataclasses import asdict
from typing import cast

import pytest

from hmc_mcp.client.core import HMCClient
from hmc_mcp.errors import HMCError
from hmc_mcp.operations.storage.resources import (
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


def _vg_client(resource: dict[str, object]) -> HMCClient:
    class _Client:
        async def list_volume_groups(self, _vios_uuid: str):
            return [{"UUID": "vg-1", "Resource": {"GroupName": "rootvg", **resource}}]

    return cast(HMCClient, _Client())


@pytest.mark.asyncio
async def test_volume_group_accepts_fractional_capacity_strings() -> None:
    """The HMC reports these fields as decimal strings with a fractional part.

    Observed live on V10R3 / POWER10: rejecting them failed the whole
    volume-group projection for a well-formed HMC reply.
    """
    groups = await list_volume_groups(
        _vg_client({"GroupCapacity": "279.25", "FreeSpace": "558.9111"}), VIOS_UUID
    )

    assert groups[0].capacity_mib == pytest.approx(279.25)
    assert groups[0].free_space_mib == pytest.approx(558.9111)


@pytest.mark.asyncio
async def test_volume_group_keeps_integral_values_as_int() -> None:
    """An integral value stays an int so rendered output gains no '.0' suffix."""
    groups = await list_volume_groups(
        _vg_client({"GroupCapacity": "102400", "FreeSpace": "0"}), VIOS_UUID
    )

    assert groups[0].capacity_mib == 102400
    assert not isinstance(groups[0].capacity_mib, float)
    assert groups[0].free_space_mib == 0
    assert not isinstance(groups[0].free_space_mib, float)


@pytest.mark.asyncio
async def test_volume_group_keeps_integral_float_strings_as_int() -> None:
    groups = await list_volume_groups(
        _vg_client({"GroupCapacity": "279.0"}), VIOS_UUID
    )

    assert groups[0].capacity_mib == 279
    assert not isinstance(groups[0].capacity_mib, float)


@pytest.mark.asyncio
async def test_volume_group_parses_a_long_whole_number_exactly() -> None:
    """A whole number stays exact at any width; float() would saturate to inf."""
    digits = "9" * 400

    groups = await list_volume_groups(_vg_client({"GroupCapacity": digits}), VIOS_UUID)

    assert groups[0].capacity_mib == int(digits)


@pytest.mark.asyncio
async def test_volume_group_absent_capacity_stays_none() -> None:
    groups = await list_volume_groups(_vg_client({}), VIOS_UUID)

    assert groups[0].capacity_mib is None
    assert groups[0].free_space_mib is None


@pytest.mark.parametrize(
    "value",
    ["", "abc", "279.25.1", "-5", "1e5", "NaN", "Infinity", "279,25", " 279.25 "],
)
@pytest.mark.asyncio
async def test_volume_group_rejects_non_decimal_capacity(value: str) -> None:
    """A value that is not a plain decimal still raises, naming operation and field."""
    with pytest.raises(HMCError, match="list_volume_groups returned an invalid GroupCapacity"):
        await list_volume_groups(_vg_client({"GroupCapacity": value}), VIOS_UUID)


@pytest.mark.asyncio
async def test_volume_group_rejects_bool_capacity() -> None:
    """`True` is an `int` subclass; admitting it would report a capacity of 1."""
    with pytest.raises(HMCError, match="GroupCapacity"):
        await list_volume_groups(_vg_client({"GroupCapacity": True}), VIOS_UUID)


@pytest.mark.asyncio
async def test_optical_media_accepts_fractional_size() -> None:
    """MediaSize routes through the same helper, so it gains the same tolerance."""

    class _Client:
        async def list_optical_media(self, _vios_uuid: str, _vg_uuid: str):
            return [{"MediaName": "install.iso", "MediaSize": "1024.5", "MediaType": "ISO"}]

    media = await list_optical_media(cast(HMCClient, _Client()), VIOS_UUID, "vg-1")

    assert media[0].size_mib == pytest.approx(1024.5)
