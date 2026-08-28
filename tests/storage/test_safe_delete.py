"""Tests for safe ISO and media-repository deletion operations."""

import httpx
import pytest

from unittest.mock import AsyncMock

from conftest import make_config

from hmc_mcp.client.core import HMCClient
from hmc_mcp.errors import HMCError
from hmc_mcp.operations.storage import (
    delete_media_repository,
    delete_optical_media,
    delete_virtual_disk,
)

VIOS_UUID = "00000000-0000-0000-0000-000000000003"
VG_UUID = "vg-uuid-002"
MEDIA_NAME = "test-image.iso"

VG_PATH = f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup/{VG_UUID}"
VIOS_PATH = f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}?group=ViosSCSIMapping"

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

# &#10; (newline) and &#9; (tab) are legal XML character references, so an HMC
# can hand back a MediaName carrying raw control characters.
HOSTILE_MEDIA_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <content>
      <VolumeGroup xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <MediaRepositories>
          <VirtualMediaRepository>
            <VirtualOpticalMedia>
              <MediaName>evil&#10;image&#9;.iso</MediaName>
              <MediaSize>4500</MediaSize>
            </VirtualOpticalMedia>
          </VirtualMediaRepository>
        </MediaRepositories>
      </VolumeGroup>
    </content>
  </entry>
</feed>"""

HOSTILE_MOUNTED_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <content>
      <VirtualIOServer>
        <VirtualSCSIMappings>
          <VirtualSCSIMapping>
            <UUID>mapping-001</UUID>
            <Storage>
              <VirtualOpticalMedia>
                <MediaName>evil'name.iso</MediaName>
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
            await delete_media_repository(hmc, None, VIOS_UUID, VG_UUID)


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
        result = await delete_media_repository(hmc, None, VIOS_UUID, VG_UUID)

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
            await delete_optical_media(hmc, None, VIOS_UUID, VG_UUID, MEDIA_NAME)


@pytest.mark.asyncio
async def test_delete_optical_media_succeeds_when_unmounted(mock_hmc):
    """delete_optical_media succeeds when no optical mappings reference it.

    delete_optical_media:
      1. GETs ViosSCSIMapping path (list_optical_mappings) — returns no mappings.
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
        result = await delete_optical_media(hmc, None, VIOS_UUID, VG_UUID, MEDIA_NAME)

    assert result is not None


@pytest.mark.asyncio
async def test_delete_virtual_disk_refusal_is_repr_quoted(mock_hmc):
    """The refusal message repr-quotes the disk and LPAR names it echoes."""
    disk_name = "evil'name.iso"
    mapping = {
        "Storage": {
            "VirtualDisk": {
                "href": (
                    f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup/"
                    f"{VG_UUID}/VirtualDisk/{disk_name}"
                )
            }
        },
        "AssociatedLogicalPartition": {"PartitionName": "lpar-001"},
    }

    config = make_config()
    async with HMCClient(config) as hmc:
        hmc.list_storage_mappings = AsyncMock(return_value=[mapping])
        with pytest.raises(HMCError) as exc_info:
            await delete_virtual_disk(hmc, None, VIOS_UUID, VG_UUID, disk_name)

    message = str(exc_info.value)
    assert repr(disk_name) in message
    assert repr("lpar-001") in message


@pytest.mark.asyncio
async def test_delete_media_repository_refusal_is_repr_quoted(mock_hmc):
    """HMC-derived MediaName text cannot carry control characters into str()."""
    mock_hmc.get(VG_PATH).mock(
        return_value=httpx.Response(200, text=HOSTILE_MEDIA_FEED)
    )

    config = make_config()
    async with HMCClient(config) as hmc:
        with pytest.raises(HMCError) as exc_info:
            await delete_media_repository(hmc, None, VIOS_UUID, VG_UUID)

    message = str(exc_info.value)
    hostile_names = "evil\nimage\t.iso"
    assert repr(hostile_names) in message
    assert not any(
        ord(ch) < 0x20 or ord(ch) == 0x7F or ch in "\u2028\u2029" for ch in message
    )


@pytest.mark.asyncio
async def test_delete_optical_media_refusal_is_repr_quoted(mock_hmc):
    """The mounted-media refusal repr-quotes the names it echoes."""
    media_name = "evil'name.iso"
    mock_hmc.get(VIOS_PATH).mock(
        return_value=httpx.Response(200, text=HOSTILE_MOUNTED_FEED)
    )

    config = make_config()
    async with HMCClient(config) as hmc:
        with pytest.raises(HMCError) as exc_info:
            await delete_optical_media(hmc, None, VIOS_UUID, VG_UUID, media_name)

    message = str(exc_info.value)
    assert repr(media_name) in message
