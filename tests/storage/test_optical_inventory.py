"""Transport-layer tests for optical mapping inventory and operations."""

import pytest



@pytest.fixture
def mock_hmc(mock_hmc):
    """Configure mock HMC with optical mapping responses."""
    
    # Mock list_optical_mappings response
    optical_mappings_feed = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
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
    
    mock_hmc.get(
        "https://example.com:12443/rest/api/uom/VirtualIOServer/vios-uuid-001?group=ViosStorageDetail",
        body=optical_mappings_feed,
        status=200,
    )
    
    # Mock create_optical_mapping response
    create_optical_feed = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
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
    
    mock_hmc.post(
        "https://example.com:12443/rest/api/uom/VirtualIOServer/vios-uuid-001",
        body=create_optical_feed,
        status=200,
    )
    
    return mock_hmc


async def test_list_optical_mappings_filters_optical_only(mock_hmc):
    """list_optical_mappings returns only VirtualOpticalMedia-backed mappings."""
    from hmc_mcp.client_storage import StorageMixin
    
    class TestClient(StorageMixin):
        config = mock_hmc.config
        
        async def _get(self, path: str, resource_type: str, **kwargs):
            return await mock_hmc.aiohttp.get(f"https://example.com:12443{path}")
    
    client = TestClient()
    mappings = await client.list_optical_mappings("vios-uuid-001")
    
    assert len(mappings) == 2
    assert all(m.get("Storage", {}).get("VirtualOpticalMedia") for m in mappings)
    
    media_names = [m["Storage"]["VirtualOpticalMedia"]["MediaName"] for m in mappings]
    assert "aix72.iso" in media_names
    assert "rhel8.iso" in media_names


async def test_list_optical_mappings_filters_by_lpar(mock_hmc):
    """list_optical_mappings can filter by LPAR."""
    from hmc_mcp.client_storage import StorageMixin
    
    class TestClient(StorageMixin):
        config = mock_hmc.config
        
        async def _get(self, path: str, resource_type: str, **kwargs):
            return await mock_hmc.aiohttp.get(f"https://example.com:12443{path}")
    
    client = TestClient()
    mappings = await client.list_optical_mappings("vios-uuid-001", "lpar-uuid-001")
    
    assert len(mappings) == 2
    for m in mappings:
        assert m["AssociatedLogicalPartition"]["@href"] == "/rest/api/uom/LogicalPartition/lpar-uuid-001"


async def test_list_optical_mappings_empty_response(mock_hmc):
    """list_optical_mappings returns empty list for no mappings."""
    from hmc_mcp.client_storage import StorageMixin
    
    empty_feed = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
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
    
    mock_hmc.get(
        "https://example.com:12443/rest/api/uom/VirtualIOServer/vios-uuid-001?group=ViosStorageDetail",
        body=empty_feed,
        status=200,
    )
    
    class TestClient(StorageMixin):
        config = mock_hmc.config
        
        async def _get(self, path: str, resource_type: str, **kwargs):
            return await mock_hmc.aiohttp.get(f"https://example.com:12443{path}")
    
    client = TestClient()
    mappings = await client.list_optical_mappings("vios-uuid-001")
    
    assert mappings == []


async def test_create_optical_mapping_submits_document(mock_hmc):
    """create_optical_mapping POSTs a valid optical mapping document."""
    from hmc_mcp.client_storage import StorageMixin
    
    class TestClient(StorageMixin):
        config = mock_hmc.config
        base_url = "https://example.com:12443"
        
        def get_lpar_link(self, lpar_uuid: str) -> str:
            return f"{self.base_url}/rest/api/uom/LogicalPartition/{lpar_uuid}"
        
        async def _post(self, path: str, xml: str, **kwargs):
            assert "VirtualOpticalMedia" in xml
            assert "<MediaName>test.iso</MediaName>" in xml
            assert "/rest/api/uom/LogicalPartition/lpar-uuid-001" in xml
            return await mock_hmc.aiohttp.post(f"https://example.com:12443{path}", body=xml)
    
    client = TestClient()
    result = await client.create_optical_mapping("vios-uuid-001", "test.iso", "lpar-uuid-001")
    
    assert result is not None
    assert result["UUID"] == "mapping-uuid-new-001"
    assert result["Storage"]["VirtualOpticalMedia"]["MediaName"] == "test.iso"


async def test_delete_optical_mapping_deletes_by_uuid(mock_hmc):
    """delete_optical_mapping DELETEs the mapping by UUID."""
    from hmc_mcp.client_storage import StorageMixin
    
    delete_route = mock_hmc.delete(
        "https://example.com:12443/rest/api/uom/VirtualIOServer/vios-uuid-001/VirtualSCSIMapping/mapping-uuid-001",
        status=204,
    )
    
    class TestClient(StorageMixin):
        config = mock_hmc.config
        
        async def _delete(self, path: str):
            return await mock_hmc.aiohttp.delete(f"https://example.com:12443{path}")
    
    client = TestClient()
    await client.delete_optical_mapping("vios-uuid-001", "mapping-uuid-001")
    
    assert delete_route.called
