"""Tests for safe ISO and media-repository deletion operations."""

import httpx
import pytest

from conftest import make_config

from hmc_mcp.client import HMCClient
from hmc_mcp.errors import HMCError
from hmc_mcp.operations_storage import delete_media_repository, delete_optical_media

VIOS_UUID = "00000000-0000-0000-0000-000000000003"
VG_UUID = "vg-uuid-002"
MEDIA_NAME = "test-image.iso"

VG_PATH = f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup/{VG_UUID}"
VIOS_PATH = f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}?group=ViosStorageDetail"

EMPTY_REPO_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <content>
      <VolumeGroup xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <VirtualMediaRepository>
          <RepositoryName>VMLibrary</RepositoryName>
        </VirtualMediaRepository>
      </VolumeGroup>
    </content>
  </entry>
</feed>"""

NONEMPTY_REPO_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <content>
      <VolumeGroup xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <MediaRepositories>
          <VirtualMediaRepository>
            <VirtualOpticalMedia>
              <MediaName>test-image.iso</MediaName>
              <MediaSize>4500</MediaSize>
            </VirtualOpticalMedia>
          </VirtualMediaRepository>
        </MediaRepositories>
      </VolumeGroup>
    </content>
  </entry>
</feed>"""

MEDIA_VG_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <content>
      <VolumeGroup xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <MediaRepositories>
          <VirtualMediaRepository>
            <RepositoryName>VMLibrary</RepositoryName>
            <VirtualOpticalMedia>
              <MediaName>test-image.iso</MediaName>
              <MediaSize>4500</MediaSize>
            </VirtualOpticalMedia>
          </VirtualMediaRepository>
        </MediaRepositories>
      </VolumeGroup>
    </content>
  </entry>
</feed>"""

NO_MAPPINGS_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <content>
      <VirtualIOServer>
        <VirtualSCSIMappings/>
      </VirtualIOServer>
    </content>
  </entry>
</feed>"""

MOUNTED_MAPPINGS_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <content>
      <VirtualIOServer>
        <VirtualSCSIMappings>
          <VirtualSCSIMapping>
            <UUID>mapping-001</UUID>
            <Storage>
              <VirtualOpticalMedia>
                <MediaName>test-image.iso</MediaName>
              </VirtualOpticalMedia>
            </Storage>
            <AssociatedLogicalPartition rel="related" href="/rest/api/uom/LogicalPartition/lpar-001"/>
          </VirtualSCSIMapping>
        </VirtualSCSIMappings>
      </VirtualIOServer>
    </content>
  </entry>
</feed>"""


@pytest.mark.asyncio
async def test_delete_media_repository_refuses_nonempty(mock_hmc):
    """delete_media_repository raises when repository contains images."""
    mock_hmc.get(VG_PATH).mock(
        return_value=httpx.Response(200, text=NONEMPTY_REPO_FEED)
    )

    config = make_config()
    async with HMCClient(config) as hmc:
        with pytest.raises(HMCError, match="contains 1 image"):
            await delete_media_repository(hmc, VIOS_UUID, VG_UUID)


@pytest.mark.asyncio
async def test_delete_media_repository_succeeds_when_empty(mock_hmc):
    """delete_media_repository succeeds when repository is empty."""
    mock_hmc.get(VG_PATH).mock(
        return_value=httpx.Response(200, text=EMPTY_REPO_FEED)
    )
    mock_hmc.post(VG_PATH).mock(
        return_value=httpx.Response(200, text=EMPTY_REPO_FEED)
    )

    config = make_config()
    async with HMCClient(config) as hmc:
        result = await delete_media_repository(hmc, VIOS_UUID, VG_UUID)

    assert result == VIOS_UUID


@pytest.mark.asyncio
async def test_delete_optical_media_refuses_when_mounted(mock_hmc):
    """delete_optical_media raises when media is mounted to an LPAR."""
    mock_hmc.get(VIOS_PATH).mock(
        return_value=httpx.Response(200, text=MOUNTED_MAPPINGS_FEED)
    )

    config = make_config()
    async with HMCClient(config) as hmc:
        with pytest.raises(HMCError, match="mounted on 1 LPAR"):
            await delete_optical_media(hmc, VIOS_UUID, VG_UUID, MEDIA_NAME)


@pytest.mark.asyncio
async def test_delete_optical_media_succeeds_when_unmounted(mock_hmc):
    """delete_optical_media succeeds when no optical mappings reference it.

    delete_optical_media:
      1. GETs ViosStorageDetail path (list_optical_mappings) — returns no mappings.
      2. GETs VG_PATH (hmc.delete_optical_media read step) — returns MEDIA_VG_FEED.
      3. POSTs VG_PATH with the media node removed.
    """
    mock_hmc.get(VIOS_PATH).mock(
        return_value=httpx.Response(200, text=NO_MAPPINGS_FEED)
    )
    mock_hmc.get(VG_PATH).mock(
        return_value=httpx.Response(200, text=MEDIA_VG_FEED)
    )
    mock_hmc.post(VG_PATH).mock(
        return_value=httpx.Response(200, text=EMPTY_REPO_FEED)
    )

    config = make_config()
    async with HMCClient(config) as hmc:
        result = await delete_optical_media(hmc, VIOS_UUID, VG_UUID, MEDIA_NAME)

    assert result is not None
