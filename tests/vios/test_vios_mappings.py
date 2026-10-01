"""Tests for VIOS storage-detail (device mapping) tool."""

import httpx
import pytest
from conftest import live_fixture, make_config

from hmcpctl.client.core import HMCClient

VIOS_UUID = "00000005-ABCD-4EF0-8ABC-000000000005"
COMMA = live_fixture("rest-vios-groups-comma")
REPEAT = live_fixture("rest-vios-groups-repeat")


def test_repeated_group_parameter_drops_the_second_group():
    """V10R3 honours only the first of two repeated ``group`` parameters (#1202).

    The same VIOS read with ``group=A&group=B`` carries no ViosFCMapping group;
    the comma form carries both. Pinned so the client's choice of form stays
    grounded in the capture.
    """
    assert "VirtualFibreChannelMappings" not in REPEAT["body"]
    assert 'group="ViosFCMapping"' in COMMA["body"]
    assert COMMA["path"].endswith("?group=ViosSCSIMapping,ViosFCMapping")


@pytest.mark.asyncio
async def test_get_vios_storage_detail(mock_hmc):
    """get_vios_storage_detail asks for both mapping groups in one comma list."""
    route = mock_hmc.get(COMMA["path"]).mock(
        return_value=httpx.Response(200, text=COMMA["body"])
    )

    async with HMCClient(make_config()) as hmc:
        result = await hmc.get_vios_storage_detail(VIOS_UUID)

    assert route.called
    assert route.calls.last.request.url.query == b"group=ViosSCSIMapping,ViosFCMapping"
    assert result is not None
    assert result["UUID"] == VIOS_UUID
    resource = result["Resource"]
    assert resource["PartitionName"] == "sys-R1-vios1"
    mapping = resource["VirtualSCSIMappings"]["VirtualSCSIMapping"]
    assert mapping["Storage"]["VirtualDisk"]["DiskName"] == "dev-297"
    # The captured VIOS has no NPIV mappings; the group is present and empty.
    assert "VirtualFibreChannelMapping" not in resource["VirtualFibreChannelMappings"]


@pytest.mark.asyncio
async def test_get_vios_storage_detail_not_found(mock_hmc):
    """get_vios_storage_detail returns None on 204 (empty)."""
    mock_hmc.get(COMMA["path"]).mock(return_value=httpx.Response(204))

    async with HMCClient(make_config()) as hmc:
        result = await hmc.get_vios_storage_detail(VIOS_UUID)

    assert result is None
