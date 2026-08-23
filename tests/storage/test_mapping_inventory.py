"""Transport-layer tests for VirtualSCSI mapping inventory.

Regression coverage for issue #348: list_storage_mappings must read the
mapping block from the parsed Resource (not the Atom entry) and the
lpar_uuid filters must match the href key element_to_dict actually
produces, so both previously-dead paths fire.
"""

import httpx
import pytest

from conftest import make_config

from hmc_mcp.client import HMCClient, HMCError

VIOS_UUID = "00000000-0000-0000-0000-000000000003"

MAPPINGS_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:uom="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
  <entry>
    <content>
      <VirtualIOServer>
        <VirtualSCSIMappings>
          <VirtualSCSIMapping>
            <Metadata><Atom/></Metadata>
            <UUID>mapping-uuid-disk-001</UUID>
            <Storage>
              <VirtualDisk>
                <DiskName>lv_boot</DiskName>
              </VirtualDisk>
            </Storage>
            <TargetDevice>vhost0</TargetDevice>
            <AssociatedLogicalPartition rel="related" href="/rest/api/uom/LogicalPartition/lpar-uuid-001"/>
          </VirtualSCSIMapping>
          <VirtualSCSIMapping>
            <Metadata><Atom/></Metadata>
            <UUID>mapping-uuid-pv-001</UUID>
            <Storage>
              <PhysicalVolume>
                <VolumeName>hdisk5</VolumeName>
              </PhysicalVolume>
            </Storage>
            <AssociatedLogicalPartition rel="related" href="/rest/api/uom/LogicalPartition/lpar-uuid-002"/>
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

# Reuses the optical inventory feed shape: two optical mappings split
# across two LPARs plus one disk mapping, to prove the lpar_uuid filter
# on list_optical_mappings bites.
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
            <AssociatedLogicalPartition rel="related" href="/rest/api/uom/LogicalPartition/lpar-uuid-001"/>
          </VirtualSCSIMapping>
          <VirtualSCSIMapping>
            <Metadata><Atom/></Metadata>
            <UUID>mapping-uuid-optical-002</UUID>
            <Storage>
              <VirtualOpticalMedia>
                <MediaName>rhel8.iso</MediaName>
              </VirtualOpticalMedia>
            </Storage>
            <AssociatedLogicalPartition rel="related" href="/rest/api/uom/LogicalPartition/lpar-uuid-002"/>
          </VirtualSCSIMapping>
        </VirtualSCSIMappings>
      </VirtualIOServer>
    </content>
  </entry>
</feed>
"""

VIOS_PATH = f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}?group=ViosSCSIMapping"


@pytest.mark.asyncio
async def test_list_storage_mappings_reads_resource(mock_hmc):
    """Mappings live under entries[0]["Resource"], not on the entry itself."""
    mock_hmc.get(VIOS_PATH).mock(
        return_value=httpx.Response(200, text=MAPPINGS_FEED)
    )

    config = make_config()
    async with HMCClient(config) as hmc:
        mappings = await hmc.list_storage_mappings(VIOS_UUID)

    assert len(mappings) == 2
    names = sorted(
        next(iter(m["Storage"].values()))["VolumeName"]
        if "PhysicalVolume" in m["Storage"]
        else next(iter(m["Storage"].values()))["DiskName"]
        for m in mappings
    )
    assert names == ["hdisk5", "lv_boot"]


@pytest.mark.asyncio
async def test_list_storage_mappings_empty(mock_hmc):
    """list_storage_mappings returns empty list when no mappings exist."""
    mock_hmc.get(VIOS_PATH).mock(
        return_value=httpx.Response(200, text=EMPTY_MAPPINGS_FEED)
    )

    config = make_config()
    async with HMCClient(config) as hmc:
        mappings = await hmc.list_storage_mappings(VIOS_UUID)

    assert mappings == []


@pytest.mark.asyncio
async def test_list_storage_mappings_propagates_bad_request(mock_hmc):
    """A rejected documented group is an API error, not an empty inventory."""
    mock_hmc.get(VIOS_PATH).mock(return_value=httpx.Response(400, text="bad request"))

    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError) as raised:
            await hmc.list_storage_mappings(VIOS_UUID)

    assert raised.value.status_code == 400


@pytest.mark.asyncio
async def test_list_storage_mappings_filters_by_lpar(mock_hmc):
    """The lpar_uuid filter matches the parsed href key and keeps only that LPAR's mappings."""
    mock_hmc.get(VIOS_PATH).mock(
        return_value=httpx.Response(200, text=MAPPINGS_FEED)
    )

    config = make_config()
    async with HMCClient(config) as hmc:
        mappings = await hmc.list_storage_mappings(VIOS_UUID, "lpar-uuid-002")

    assert len(mappings) == 1
    assert mappings[0]["UUID"] == "mapping-uuid-pv-001"
    assert (
        mappings[0]["AssociatedLogicalPartition"]["href"]
        == "/rest/api/uom/LogicalPartition/lpar-uuid-002"
    )


@pytest.mark.asyncio
async def test_list_optical_mappings_filters_by_lpar(mock_hmc):
    """list_optical_mappings' lpar_uuid filter matches the parsed href key."""
    mock_hmc.get(VIOS_PATH).mock(
        return_value=httpx.Response(200, text=OPTICAL_MAPPINGS_FEED)
    )

    config = make_config()
    async with HMCClient(config) as hmc:
        mappings = await hmc.list_optical_mappings(VIOS_UUID, "lpar-uuid-001")

    assert len(mappings) == 1
    assert mappings[0]["Storage"]["VirtualOpticalMedia"]["MediaName"] == "aix72.iso"
