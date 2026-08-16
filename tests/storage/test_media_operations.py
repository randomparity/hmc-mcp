"""Tests for media repository and optical media operations layer."""

import httpx
import pytest

from conftest import make_config

from hmc_mcp.client import HMCClient
from hmc_mcp.operations_storage import get_media_repository, list_optical_media

VIOS_UUID = "00000000-0000-0000-0000-000000000003"
VG_UUID = "vg-uuid-0001"

VG_ENTRY_WITH_REPO = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:vg-uuid-0001</id>
  <title>VolumeGroup:VMLibrary</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <VolumeGroup xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <VolumeGroupUUID>vg-uuid-0001</VolumeGroupUUID>
      <GroupName>VMLibrary</GroupName>
      <VirtualMediaRepository schemaVersion="V1_0">
        <RepositoryName>VMLibrary</RepositoryName>
        <RepositorySize>40960</RepositorySize>
      </VirtualMediaRepository>
    </VolumeGroup>
  </content>
</entry>
"""

VG_ENTRY_EMPTY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:vg-uuid-0002</id>
  <title>VolumeGroup:vg_data</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <VolumeGroup xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <VolumeGroupUUID>vg-uuid-0002</VolumeGroupUUID>
      <GroupName>vg_data</GroupName>
    </VolumeGroup>
  </content>
</entry>
"""


@pytest.mark.asyncio
async def test_get_media_repository_operation(mock_hmc):
    """get_media_repository calls client method and returns repository info."""
    route = mock_hmc.get(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup/{VG_UUID}"
    ).mock(return_value=httpx.Response(200, text=VG_ENTRY_WITH_REPO))

    async with HMCClient(make_config()) as hmc:
        result = await get_media_repository(hmc, VIOS_UUID, VG_UUID)

    assert route.called
    assert result is not None
    resource = result["Resource"]
    repo = resource["VirtualMediaRepository"]
    assert repo["RepositoryName"] == "VMLibrary"
    assert repo["RepositorySize"] == "40960"


@pytest.mark.asyncio
async def test_list_optical_media_operation(mock_hmc):
    """list_optical_media calls client method and returns optical media list."""
    vg_entry_with_media = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:vg-uuid-0001</id>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <VolumeGroup xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <VolumeGroupUUID>vg-uuid-0001</VolumeGroupUUID>
      <VirtualMediaRepository schemaVersion="V1_0">
        <RepositoryName>VMLibrary</RepositoryName>
        <RepositorySize>40960</RepositorySize>
        <VirtualOpticalMedia schemaVersion="V1_0">
          <MediaName>aix.iso</MediaName>
          <MediaSize>1400</MediaSize>
          <MediaType>BLANK</MediaType>
        </VirtualOpticalMedia>
      </VirtualMediaRepository>
    </VolumeGroup>
  </content>
</entry>
"""
    route = mock_hmc.get(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup/{VG_UUID}"
    ).mock(return_value=httpx.Response(200, text=vg_entry_with_media))

    async with HMCClient(make_config()) as hmc:
        media_list = await list_optical_media(hmc, VIOS_UUID, VG_UUID)

    assert route.called
    assert len(media_list) == 1
    assert media_list[0]["MediaName"] == "aix.iso"


@pytest.mark.asyncio
async def test_get_media_repository_none_propagates(mock_hmc):
    """get_media_repository propagates None when repository not found."""
    route = mock_hmc.get(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup/missing-vg"
    ).mock(return_value=httpx.Response(404, text=""))

    async with HMCClient(make_config()) as hmc:
        result = await get_media_repository(hmc, VIOS_UUID, "missing-vg")

    assert route.called
    assert result is None


@pytest.mark.asyncio
async def test_list_optical_media_empty_propagates(mock_hmc):
    """list_optical_media propagates empty list when no media or VG not found."""
    route = mock_hmc.get(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup/{VG_UUID}"
    ).mock(return_value=httpx.Response(200, text=VG_ENTRY_EMPTY))

    async with HMCClient(make_config()) as hmc:
        media_list = await list_optical_media(hmc, VIOS_UUID, VG_UUID)

    assert route.called
    assert media_list == []