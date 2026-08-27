"""Tests for VIOS media repository and optical media inventory operations."""

import httpx
import pytest

from conftest import make_config

from hmc_mcp.client import HMCClient

VG_ENTRY_WITH_REPO = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:vg-uuid-0001</id>
  <title>VolumeGroup:VMLibrary</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <VolumeGroup xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <VolumeGroupUUID>vg-uuid-0001</VolumeGroupUUID>
      <GroupName>VMLibrary</GroupName>
      <VirtualMediaRepository schemaVersion="V1_0">
        <Metadata><Atom/></Metadata>
        <RepositoryName>VMLibrary</RepositoryName>
        <RepositorySize>40960</RepositorySize>
        <VirtualOpticalMedia schemaVersion="V1_0">
          <Metadata><Atom/></Metadata>
          <MediaName>aix.iso</MediaName>
          <MediaSize>1400</MediaSize>
          <MediaType>BLANK</MediaType>
        </VirtualOpticalMedia>
        <VirtualOpticalMedia schemaVersion="V1_0">
          <Metadata><Atom/></Metadata>
          <MediaName>linux.iso</MediaName>
          <MediaSize>2048</MediaSize>
          <MediaType>BLANK</MediaType>
        </VirtualOpticalMedia>
      </VirtualMediaRepository>
    </VolumeGroup>
  </content>
</entry>
"""

VG_ENTRY_EMPTY_REPO = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:vg-uuid-0002</id>
  <title>VolumeGroup:VMLibrary</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <VolumeGroup xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <VolumeGroupUUID>vg-uuid-0002</VolumeGroupUUID>
      <GroupName>VMLibrary</GroupName>
      <VirtualMediaRepository schemaVersion="V1_0">
        <Metadata><Atom/></Metadata>
        <RepositoryName>VMLibrary</RepositoryName>
        <RepositorySize>8192</RepositorySize>
      </VirtualMediaRepository>
    </VolumeGroup>
  </content>
</entry>
"""

VG_ENTRY_WITHOUT_REPO = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:vg-uuid-0003</id>
  <title>VolumeGroup:data</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <VolumeGroup xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <VolumeGroupUUID>vg-uuid-0003</VolumeGroupUUID>
      <GroupName>data</GroupName>
    </VolumeGroup>
  </content>
</entry>
"""


@pytest.mark.asyncio
async def test_get_media_repository(mock_hmc):
    """get_media_repository returns the repository with capacity and media."""
    route = mock_hmc.get(
        "/rest/api/uom/VirtualIOServer/vios-uuid/VolumeGroup/vg-uuid-0001"
    ).mock(return_value=httpx.Response(200, text=VG_ENTRY_WITH_REPO))

    async with HMCClient(make_config()) as hmc:
        result = await hmc.get_media_repository("vios-uuid", "vg-uuid-0001")

    assert route.called
    assert result is not None
    resource = result["Resource"]
    repo = resource["VirtualMediaRepository"]
    assert repo["RepositoryName"] == "VMLibrary"
    assert repo["RepositorySize"] == "40960"
    assert "VirtualOpticalMedia" in repo
    assert isinstance(repo["VirtualOpticalMedia"], list)
    assert len(repo["VirtualOpticalMedia"]) == 2


@pytest.mark.asyncio
async def test_get_media_repository_empty(mock_hmc):
    """get_media_repository handles a repository with no optical media."""
    route = mock_hmc.get(
        "/rest/api/uom/VirtualIOServer/vios-uuid/VolumeGroup/vg-uuid-0002"
    ).mock(return_value=httpx.Response(200, text=VG_ENTRY_EMPTY_REPO))

    async with HMCClient(make_config()) as hmc:
        result = await hmc.get_media_repository("vios-uuid", "vg-uuid-0002")

    assert route.called
    assert result is not None
    resource = result["Resource"]
    repo = resource["VirtualMediaRepository"]
    assert repo["RepositoryName"] == "VMLibrary"
    assert repo["RepositorySize"] == "8192"
    # Empty list or absent when no media
    media = repo.get("VirtualOpticalMedia", [])
    assert isinstance(media, list)
    assert len(media) == 0


@pytest.mark.asyncio
async def test_get_media_repository_not_found(mock_hmc):
    """get_media_repository returns None when repository doesn't exist."""
    route = mock_hmc.get(
        "/rest/api/uom/VirtualIOServer/vios-uuid/VolumeGroup/missing-uuid"
    ).mock(return_value=httpx.Response(404, text=""))

    async with HMCClient(make_config()) as hmc:
        result = await hmc.get_media_repository("vios-uuid", "missing-uuid")

    assert route.called
    assert result is None


@pytest.mark.asyncio
async def test_get_media_repository_absent_from_existing_volume_group(mock_hmc):
    """An existing volume group without a repository is not a repository."""
    route = mock_hmc.get(
        "/rest/api/uom/VirtualIOServer/vios-uuid/VolumeGroup/vg-uuid-0003"
    ).mock(return_value=httpx.Response(200, text=VG_ENTRY_WITHOUT_REPO))

    async with HMCClient(make_config()) as hmc:
        result = await hmc.get_media_repository("vios-uuid", "vg-uuid-0003")

    assert route.called
    assert result is None


@pytest.mark.asyncio
async def test_list_optical_media(mock_hmc):
    """list_optical_media extracts and returns optical media entries."""
    route = mock_hmc.get(
        "/rest/api/uom/VirtualIOServer/vios-uuid/VolumeGroup/vg-uuid-0001"
    ).mock(return_value=httpx.Response(200, text=VG_ENTRY_WITH_REPO))

    async with HMCClient(make_config()) as hmc:
        media_list = await hmc.list_optical_media("vios-uuid", "vg-uuid-0001")

    assert route.called
    assert len(media_list) == 2
    assert media_list[0]["MediaName"] == "aix.iso"
    assert media_list[0]["MediaSize"] == "1400"
    assert media_list[0]["MediaType"] == "BLANK"
    assert media_list[1]["MediaName"] == "linux.iso"
    assert media_list[1]["MediaSize"] == "2048"
    assert media_list[1]["MediaType"] == "BLANK"


@pytest.mark.asyncio
async def test_list_optical_media_empty(mock_hmc):
    """list_optical_media returns empty list when no media present."""
    route = mock_hmc.get(
        "/rest/api/uom/VirtualIOServer/vios-uuid/VolumeGroup/vg-uuid-0002"
    ).mock(return_value=httpx.Response(200, text=VG_ENTRY_EMPTY_REPO))

    async with HMCClient(make_config()) as hmc:
        media_list = await hmc.list_optical_media("vios-uuid", "vg-uuid-0002")

    assert route.called
    assert media_list == []


@pytest.mark.asyncio
async def test_list_optical_media_not_found(mock_hmc):
    """list_optical_media returns empty list when VG doesn't exist."""
    route = mock_hmc.get(
        "/rest/api/uom/VirtualIOServer/vios-uuid/VolumeGroup/missing-uuid"
    ).mock(return_value=httpx.Response(404, text=""))

    async with HMCClient(make_config()) as hmc:
        media_list = await hmc.list_optical_media("vios-uuid", "missing-uuid")

    assert route.called
    assert media_list == []
