"""Transport-layer tests for optical mapping inventory and operations."""

import httpx
import pytest

from conftest import make_config

from hmc_mcp.client import HMCClient, HMCError

VIOS_UUID = "00000000-0000-0000-0000-000000000003"
SYS_UUID = "00000000-0000-0000-0000-000000000099"
LPAR_UUID = "00000000-0000-0000-0000-000000000001"

# Minimal VirtualIOServer GET response for create_optical_mapping tests.
# Must include AssociatedManagedSystem href (to extract SYS_UUID) and
# a VirtualSCSIMappings element (to append the new mapping to).
UOM_NS = "http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/"
VIOS_GET_FEED = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <content>
      <VirtualIOServer xmlns="{UOM_NS}" schemaVersion="V1_0">
        <Metadata><Atom/></Metadata>
        <UUID>{VIOS_UUID}</UUID>
        <AssociatedManagedSystem href="https://hmc.test:12443/rest/api/uom/ManagedSystem/{SYS_UUID}" rel="related"/>
        <VirtualSCSIMappings kb="CUD" kxe="false" schemaVersion="V1_0">
          <Metadata><Atom/></Metadata>
        </VirtualSCSIMappings>
      </VirtualIOServer>
    </content>
  </entry>
</feed>
"""

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

VIOS_PATH = f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}?group=ViosSCSIMapping"


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
async def test_list_optical_mappings_propagates_bad_request(mock_hmc):
    """A rejected documented group is an API error, not an empty inventory."""
    mock_hmc.get(VIOS_PATH).mock(return_value=httpx.Response(400, text="bad request"))

    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError) as raised:
            await hmc.list_optical_mappings(VIOS_UUID)

    assert raised.value.status_code == 400


@pytest.mark.asyncio
async def test_create_optical_mapping_submits_document(mock_hmc):
    """create_optical_mapping GETs the VIOS, appends mapping, POSTs to system-scoped endpoint."""
    # Step 1: GET the full VIOS document
    mock_hmc.get(f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}").mock(
        return_value=httpx.Response(200, text=VIOS_GET_FEED)
    )
    # Step 2: POST the modified document to the system-scoped endpoint
    post_route = mock_hmc.post(
        f"/rest/api/uom/ManagedSystem/{SYS_UUID}/VirtualIOServer/{VIOS_UUID}"
    ).mock(return_value=httpx.Response(200, text=CREATE_MAPPING_RESPONSE))

    config = make_config()
    async with HMCClient(config) as hmc:
        result = await hmc.create_optical_mapping(VIOS_UUID, "test.iso", LPAR_UUID)

    assert post_route.called
    req_body = post_route.calls.last.request.content.decode()
    assert "VirtualOpticalMedia" in req_body
    assert "test.iso" in req_body
    assert LPAR_UUID in req_body
    assert result is not None
    assert result["UUID"] == "mapping-uuid-new-001"
    assert result["Storage"]["VirtualOpticalMedia"]["MediaName"] == "test.iso"


# VIOS document containing one optical mapping for LPAR_UUID / test.iso
VIOS_GET_FEED_WITH_MAPPING = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <content>
      <VirtualIOServer xmlns="{UOM_NS}" schemaVersion="V1_0">
        <Metadata><Atom/></Metadata>
        <UUID>{VIOS_UUID}</UUID>
        <AssociatedManagedSystem href="https://hmc.test:12443/rest/api/uom/ManagedSystem/{SYS_UUID}" rel="related"/>
        <VirtualSCSIMappings kb="CUD" kxe="false" schemaVersion="V1_0">
          <Metadata><Atom/></Metadata>
          <VirtualSCSIMapping schemaVersion="V1_0">
            <Metadata><Atom/></Metadata>
            <AssociatedLogicalPartition kxe="false" kb="CUR"
              href="https://hmc.test:12443/rest/api/uom/ManagedSystem/{SYS_UUID}/LogicalPartition/{LPAR_UUID}"
              rel="related"/>
            <Storage kxe="false" kb="CUR">
              <VirtualOpticalMedia schemaVersion="V1_0">
                <Metadata><Atom/></Metadata>
                <MediaName kxe="false" kb="CUR">test.iso</MediaName>
                <MountType kxe="false" kb="CUD">r</MountType>
              </VirtualOpticalMedia>
            </Storage>
          </VirtualSCSIMapping>
        </VirtualSCSIMappings>
      </VirtualIOServer>
    </content>
  </entry>
</feed>
"""


@pytest.mark.asyncio
async def test_delete_optical_mapping_removes_via_read_modify_write(mock_hmc):
    """delete_optical_mapping uses read-modify-write: GETs VIOS, removes matching
    mapping for lpar_uuid+media_name, POSTs back to system-scoped endpoint."""
    # Step 1: GET the full VIOS document (contains one matching optical mapping)
    mock_hmc.get(f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}").mock(
        return_value=httpx.Response(200, text=VIOS_GET_FEED_WITH_MAPPING)
    )
    # Step 2: POST the modified document (mapping removed) to the system-scoped endpoint
    post_route = mock_hmc.post(
        f"/rest/api/uom/ManagedSystem/{SYS_UUID}/VirtualIOServer/{VIOS_UUID}"
    ).mock(return_value=httpx.Response(200, text="<feed/>"))

    config = make_config()
    async with HMCClient(config) as hmc:
        await hmc.delete_optical_mapping(VIOS_UUID, LPAR_UUID, "test.iso")

    assert post_route.called
    # The POSTed body must not contain the removed mapping
    req_body = post_route.calls.last.request.content.decode()
    assert "test.iso" not in req_body
    assert LPAR_UUID not in req_body


@pytest.mark.asyncio
async def test_delete_optical_mapping_noop_when_not_found(mock_hmc):
    """delete_optical_mapping is idempotent: no POST when mapping is absent."""
    # VIOS has an empty mappings list — nothing to remove
    mock_hmc.get(f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}").mock(
        return_value=httpx.Response(200, text=VIOS_GET_FEED)
    )
    post_route = mock_hmc.post(
        f"/rest/api/uom/ManagedSystem/{SYS_UUID}/VirtualIOServer/{VIOS_UUID}"
    ).mock(return_value=httpx.Response(200, text="<feed/>"))

    config = make_config()
    async with HMCClient(config) as hmc:
        await hmc.delete_optical_mapping(VIOS_UUID, LPAR_UUID, "nonexistent.iso")

    # No POST should have been made since there was nothing to remove
    assert not post_route.called
