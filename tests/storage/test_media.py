"""Tests for Virtual Media Repository / Virtual Optical Media (VolumeGroup POST).

The media-repository operations use a read-modify-write pattern: GET the full
VolumeGroup XML, mutate the in-memory element tree, then POST the modified XML
back.  These tests verify that the correct nodes are present in the POST body
after the mutation.
"""

import httpx
import pytest

from conftest import make_config

from hmc_mcp.client import HMCClient
from hmc_mcp.documents import (
    build_media_repository_document,
    build_virtual_optical_media_document,
)

# Minimal VolumeGroup feed — no MediaRepositories block (bare VG).
_VG_FEED_BARE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:vg-uuid</id>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <VolumeGroup xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <GroupName>clientvg1</GroupName>
        <VirtualDisks/>
      </VolumeGroup>
    </content>
  </entry>
</feed>"""

# VolumeGroup feed with an existing VMLibrary (no media inside).
_VG_FEED_WITH_VMLIB = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:vg-uuid</id>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <VolumeGroup xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <GroupName>clientvg1</GroupName>
        <MediaRepositories>
          <VirtualMediaRepository>
            <RepositoryName>VMLibrary</RepositoryName>
            <RepositorySize>7000</RepositorySize>
          </VirtualMediaRepository>
        </MediaRepositories>
      </VolumeGroup>
    </content>
  </entry>
</feed>"""

# Response after a successful POST — we just return the same bare feed.
_VG_POST_RESPONSE = _VG_FEED_BARE


def test_media_repository_document():
    """build_media_repository_document still produces valid XML (legacy builder)."""
    xml = build_media_repository_document(2048)
    assert "VirtualMediaRepository" in xml
    assert "RepositoryName" in xml and "VMLibrary" in xml
    assert "RepositorySize" in xml and ">2048<" in xml


def test_virtual_optical_media_document():
    """build_virtual_optical_media_document still produces valid XML (legacy builder)."""
    xml = build_virtual_optical_media_document("aix.iso", 1400)
    assert "VirtualOpticalMedia" in xml
    assert "MediaName" in xml and "aix.iso" in xml
    assert "MediaSize" in xml and ">1400<" in xml


@pytest.mark.asyncio
async def test_create_media_repository(mock_hmc):
    """create_media_repository GETs the VG, injects VMLibrary, then POSTs."""
    vg_path = "/rest/api/uom/VirtualIOServer/vios-uuid/VolumeGroup/vg-uuid"
    mock_hmc.get(vg_path).mock(return_value=httpx.Response(200, text=_VG_FEED_BARE))
    post_route = mock_hmc.post(vg_path).mock(
        return_value=httpx.Response(200, text=_VG_POST_RESPONSE)
    )
    async with HMCClient(make_config()) as hmc:
        await hmc.create_media_repository("vios-uuid", "vg-uuid", 2048)
    # POST body must contain the injected VMLibrary block.
    body = post_route.calls.last.request.content.decode()
    assert "VirtualMediaRepository" in body
    assert "VMLibrary" in body
    assert "2048" in body


@pytest.mark.asyncio
async def test_create_optical_media(mock_hmc):
    """create_optical_media GETs the VG, appends VirtualOpticalMedia, then POSTs."""
    vg_path = "/rest/api/uom/VirtualIOServer/vios-uuid/VolumeGroup/vg-uuid"
    mock_hmc.get(vg_path).mock(
        return_value=httpx.Response(200, text=_VG_FEED_WITH_VMLIB)
    )
    post_route = mock_hmc.post(vg_path).mock(
        return_value=httpx.Response(200, text=_VG_POST_RESPONSE)
    )
    async with HMCClient(make_config()) as hmc:
        await hmc.create_optical_media("vios-uuid", "vg-uuid", "aix.iso", 1400)
    body = post_route.calls.last.request.content.decode()
    assert "VirtualOpticalMedia" in body
    assert "aix.iso" in body
    assert "1400" in body


@pytest.mark.asyncio
async def test_delete_media_repository(mock_hmc):
    """delete_media_repository GETs the VG, removes MediaRepositories, then POSTs."""
    vg_path = "/rest/api/uom/VirtualIOServer/vios-uuid/VolumeGroup/vg-uuid"
    mock_hmc.get(vg_path).mock(
        return_value=httpx.Response(200, text=_VG_FEED_WITH_VMLIB)
    )
    post_route = mock_hmc.post(vg_path).mock(
        return_value=httpx.Response(200, text=_VG_POST_RESPONSE)
    )
    async with HMCClient(make_config()) as hmc:
        await hmc.delete_media_repository("vios-uuid", "vg-uuid")
    # VMLibrary must be absent from the POST body.
    body = post_route.calls.last.request.content.decode()
    assert "MediaRepositories" not in body
    assert post_route.called
