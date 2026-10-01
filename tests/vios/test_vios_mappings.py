"""Tests for VIOS storage-detail (device mapping) tool."""

import httpx
import pytest
from conftest import live_fixture, live_response, make_config

from hmcpctl.client.core import HMCClient
from hmcpctl.errors import HMCError

VIOS_UUID = "00000005-ABCD-4EF0-8ABC-000000000005"
COMMA = live_fixture("rest-vios-groups-comma")


@pytest.mark.parametrize("level", ["", "-v11r2"], ids=["V10R3", "V11R2"])
def test_repeated_group_parameter_drops_the_second_group(level):
    """V10R3 and V11R2 honour only the first of two repeated ``group`` parameters.

    The same VIOS read with ``group=A&group=B`` carries no ViosFCMapping group;
    the comma form carries both (#1202). Pinned so the client's choice of form
    stays grounded in the captures.
    """
    repeat = live_fixture(f"rest-vios-groups-repeat{level}")
    comma = live_fixture(f"rest-vios-groups-comma{level}")
    assert "VirtualFibreChannelMappings" not in repeat["body"]
    assert 'group="ViosFCMapping"' in comma["body"]
    assert comma["path"].endswith("?group=ViosSCSIMapping,ViosFCMapping")


@pytest.mark.asyncio
async def test_list_vios_reports_the_hmc_side_viosstorage_failure(mock_hmc):
    """A V11R2 HMC that cannot reach a VIOS answers the VIOS feed with 500 (#1202).

    The request is the one every other captured HMC answers with 200; the HMC
    names the VIOS it could not query, and that message reaches the caller.
    """
    path, response = live_response("rest-vios-feed-500-v11r2")
    mock_hmc.get(path).mock(return_value=response)

    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError) as raised:
            await hmc.list_vios("00000001-abcd-4ef0-8abc-000000000001")

    assert raised.value.status_code == 500
    assert "Error occurred while querying for ViosStorage from VIOS" in str(
        raised.value
    )
    assert "Data cannot be retrieved from VIOS" in str(raised.value)


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
