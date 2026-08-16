"""Transport-layer tests for optical mapping inventory and operations."""

import httpx
import pytest

from conftest import make_config

from hmc_mcp.client import HMCClient

VIOS_UUID = "00000000-0000-0000-0000-000000000003"
LPAR_UUID = "00000000-0000-0000-0000-000000000001"

OPTICAL_MAPPINGS_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:uom="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
  <entry>
    <content>
      <VirtualIOServer>
        <VirtualSCSIMappings>
          <VirtualSCSIMapping>
            <Metadata><Atom/></Metadata>
            <UUID>mapping-uuid-optical-001</UUID>
            <Storage>
              <VirtualOpticalMedia>
                <MediaName>aix72.iso</MediaName>
              </VirtualOpticalMedia>
            </Storage>
            <TargetDevice>cd0</TargetDevice>
            <AssociatedLogicalPartition rel="related" href="/rest/api/uom/LogicalPartition/lpar-uuid-001"/>
          </VirtualSCSIMapping>
          <VirtualSCSIMapping>
            <Metadata><Atom/></Metadata>
            <UUID>mapping-uuid-disk-001</UUID>
            <Storage>
              <VirtualDisk>
                <DiskName>lv_boot</DiskName>
              </VirtualDisk>
            </Storage>
            <AssociatedLogicalPartition rel="related" href="/rest/api/uom/LogicalPartition/lpar-uuid-002"/>
          </VirtualSCSIMapping>
          <VirtualSCSIMapping>
            <Metadata><Atom/></Metadata>
            <UUID>mapping-uuid-optical-002</UUID>
            <Storage>
              <VirtualOpticalMedia>
                <MediaName>rhel8.iso</MediaName>
              </VirtualOpticalMedia>
            </Storage>
            <AssociatedLogicalPartition rel="related" href="/rest/api/uom/LogicalPartition/lpar-uuid-001"/>
          </VirtualSCSIMapping>
        </VirtualSCSIMappings>
      </VirtualIOServer>
    </content>
  </entry>
</feed>
"""

EMPTY_MAPPINGS_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <content>
      <VirtualIOServer>
        <VirtualSCSIMappings/>
      </VirtualIOServer>
    </content>
  </entry>
</feed>
"""

CREATE_MAPPING_RESPONSE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <content>
      <VirtualSCSIMapping>
        <UUID>mapping-uuid-new-001</UUID>
        <Storage>
          <VirtualOpticalMedia>
            <MediaName>test.iso</MediaName>
          </VirtualOpticalMedia>
        </Storage>
        <TargetDevice>cd1</TargetDevice>
        <AssociatedLogicalPartition rel="related" href="/rest/api/uom/LogicalPartition/lpar-uuid-001"/>
      </VirtualSCSIMapping>
    </content>
  </entry>
</feed>
"""

VIOS_PATH = f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}?group=ViosStorageDetail"


@pytest.mark.asyncio
async def test_list_optical_mappings_filters_optical_only(mock_hmc):
    """list_optical_mappings returns only VirtualOpticalMedia-backed mappings."""
    mock_hmc.get(VIOS_PATH).mock(
        return_value=httpx.Response(200, text=OPTICAL_MAPPINGS_FEED)
    )

    config = make_config()
    async with HMCClient(config) as hmc:
        mappings = await hmc.list_optical_mappings(VIOS_UUID)

    assert len(mappings) == 2
    assert all(m.get("Storage", {}).get("VirtualOpticalMedia") for m in mappings)

    media_names = [m["Storage"]["VirtualOpticalMedia"]["MediaName"] for m in mappings]
    assert "aix72.iso" in media_names
    assert "rhel8.iso" in media_names


@pytest.mark.asyncio
async def test_list_optical_mappings_empty(mock_hmc):
    """list_optical_mappings returns empty list for no mappings."""
    mock_hmc.get(VIOS_PATH).mock(
        return_value=httpx.Response(200, text=EMPTY_MAPPINGS_FEED)
    )

    config = make_config()
    async with HMCClient(config) as hmc:
        mappings = await hmc.list_optical_mappings(VIOS_UUID)

    assert mappings == []


@pytest.mark.asyncio
async def test_create_optical_mapping_submits_document(mock_hmc):
    """create_optical_mapping POSTs a valid optical mapping document."""
    mock_hmc.post(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}"
    ).mock(return_value=httpx.Response(200, text=CREATE_MAPPING_RESPONSE))

    config = make_config()
    async with HMCClient(config) as hmc:
        result = await hmc.create_optical_mapping(VIOS_UUID, "test.iso", LPAR_UUID)

    assert result is not None
    assert result["UUID"] == "mapping-uuid-new-001"
    assert result["Storage"]["VirtualOpticalMedia"]["MediaName"] == "test.iso"


@pytest.mark.asyncio
async def test_delete_optical_mapping_deletes_by_uuid(mock_hmc):
    """delete_optical_mapping DELETEs the mapping by UUID."""
    delete_route = mock_hmc.delete(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VirtualSCSIMapping/mapping-uuid-001"
    ).mock(return_value=httpx.Response(204, text=""))

    config = make_config()
    async with HMCClient(config) as hmc:
        await hmc.delete_optical_mapping(VIOS_UUID, "mapping-uuid-001")

    assert delete_route.called
