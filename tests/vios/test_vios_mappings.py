"""Tests for VIOS storage-detail (device mapping) tool."""

import httpx
import pytest

from hmc_mcp.client.core import HMCClient

from conftest import make_config

BASE = "https://hmc.test"

# Minimal VIOS mapping entry with a vSCSI server mapping and an NPIV port mapping.
VIOS_STORAGE_DETAIL_ENTRY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:vios-uuid-1</id>
  <title>VirtualIOServer:vios1</title>
  <link rel="SELF" href="{base}/rest/api/uom/VirtualIOServer/vios-uuid-1"/>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <VirtualIOServer xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <PartitionName>vios1</PartitionName>
      <VirtualSCSIMappings>
        <VirtualSCSIMapping>
          <AssociatedLogicalPartition href="{base}/rest/api/uom/LogicalPartition/lpar-uuid-1"/>
          <ServerAdapter>
            <VirtualSlotNumber>2</VirtualSlotNumber>
          </ServerAdapter>
          <Storage>
            <PhysicalVolume>
              <VolumeName>hdisk5</VolumeName>
            </PhysicalVolume>
          </Storage>
        </VirtualSCSIMapping>
      </VirtualSCSIMappings>
      <VirtualFibreChannelMappings>
        <VirtualFibreChannelMapping>
          <AssociatedLogicalPartition href="{base}/rest/api/uom/LogicalPartition/lpar-uuid-2"/>
          <Port>
            <WWPNPair>C05076099999AAA0 C05076099999AAA1</WWPNPair>
          </Port>
        </VirtualFibreChannelMapping>
      </VirtualFibreChannelMappings>
    </VirtualIOServer>
  </content>
</entry>
""".format(base=BASE)



@pytest.mark.asyncio
async def test_get_vios_storage_detail(mock_hmc):
    """get_vios_storage_detail requests both documented mapping groups."""
    route = mock_hmc.get(
        "/rest/api/uom/VirtualIOServer/vios-uuid-1",
        params=[("group", "ViosSCSIMapping"), ("group", "ViosFCMapping")],
    ).mock(return_value=httpx.Response(200, text=VIOS_STORAGE_DETAIL_ENTRY))

    async with HMCClient(make_config()) as hmc:
        result = await hmc.get_vios_storage_detail("vios-uuid-1")

    assert route.called
    assert result is not None
    assert result["UUID"] == "vios-uuid-1"
    resource = result["Resource"]
    assert resource["PartitionName"] == "vios1"
    # vSCSI mapping present
    mappings = resource["VirtualSCSIMappings"]["VirtualSCSIMapping"]
    assert isinstance(mappings, dict)  # single mapping → dict, not list
    assert mappings["Storage"]["PhysicalVolume"]["VolumeName"] == "hdisk5"
    # NPIV mapping present
    fc_mappings = resource["VirtualFibreChannelMappings"]["VirtualFibreChannelMapping"]
    assert isinstance(fc_mappings, dict)
    assert "AAA0" in fc_mappings["Port"]["WWPNPair"]


@pytest.mark.asyncio
async def test_get_vios_storage_detail_not_found(mock_hmc):
    """get_vios_storage_detail returns None on 204 (empty)."""
    mock_hmc.get(
        "/rest/api/uom/VirtualIOServer/missing-uuid",
        params=[("group", "ViosSCSIMapping"), ("group", "ViosFCMapping")],
    ).mock(return_value=httpx.Response(204))

    async with HMCClient(make_config()) as hmc:
        result = await hmc.get_vios_storage_detail("missing-uuid")

    assert result is None
