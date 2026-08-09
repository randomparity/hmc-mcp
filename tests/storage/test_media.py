"""Tests for Virtual Media Repository / Virtual Optical Media (VolumeGroup POST)."""

import httpx
import pytest

from conftest import make_config

from hmc_mcp.client import HMCClient
from hmc_mcp.documents import (
    build_media_repository_document,
    build_virtual_optical_media_document,
)

VIOS_ENTRY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:vios-uuid</id>
  <title>VirtualIOServer:vios1</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <VirtualIOServer xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <PartitionName>vios1</PartitionName>
    </VirtualIOServer>
  </content>
</entry>
"""


def test_media_repository_document():
    xml = build_media_repository_document(2048)
    assert "VirtualMediaRepository" in xml
    assert "RepositoryName" in xml and "VMLibrary" in xml
    assert "RepositorySize" in xml and ">2048<" in xml


def test_virtual_optical_media_document():
    xml = build_virtual_optical_media_document("aix.iso", 1400)
    assert "VirtualOpticalMedia" in xml
    assert "MediaName" in xml and "aix.iso" in xml
    assert "MediaSize" in xml and ">1400<" in xml


@pytest.mark.asyncio
async def test_create_media_repository(mock_hmc):
    route = mock_hmc.post(
        "/rest/api/uom/VirtualIOServer/vios-uuid/VolumeGroup/vg-uuid"
    ).mock(return_value=httpx.Response(200, text=VIOS_ENTRY))
    async with HMCClient(make_config()) as hmc:
        result = await hmc.create_media_repository("vios-uuid", "vg-uuid", 2048)
    body = route.calls.last.request.content.decode()
    assert "VirtualMediaRepository" in body and "VMLibrary" in body
    assert result is not None


@pytest.mark.asyncio
async def test_create_optical_media(mock_hmc):
    route = mock_hmc.post(
        "/rest/api/uom/VirtualIOServer/vios-uuid/VolumeGroup/vg-uuid"
    ).mock(return_value=httpx.Response(200, text=VIOS_ENTRY))
    async with HMCClient(make_config()) as hmc:
        await hmc.create_optical_media("vios-uuid", "vg-uuid", "aix.iso", 1400)
    body = route.calls.last.request.content.decode()
    assert "VirtualOpticalMedia" in body and "aix.iso" in body and ">1400<" in body


@pytest.mark.asyncio
async def test_delete_media_repository(mock_hmc):
    route = mock_hmc.post(
        "/rest/api/uom/VirtualIOServer/vios-uuid/VolumeGroup/vg-uuid"
    ).mock(return_value=httpx.Response(200, text=VIOS_ENTRY))
    async with HMCClient(make_config()) as hmc:
        await hmc.delete_media_repository("vios-uuid", "vg-uuid")
    assert route.called
