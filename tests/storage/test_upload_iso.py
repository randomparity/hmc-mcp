"""Tests for ISO upload operation via HMC file broker."""

import hashlib
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from conftest import make_config

from hmc_mcp.client import HMCClient
from hmc_mcp.errors import HMCError
from hmc_mcp.operations_storage import upload_iso

# Test constants
VIOS_UUID = "00000000-0000-0000-0000-000000000003"
VG_UUID = "vg-uuid-002"
MEDIA_NAME = "test-image.iso"
TEST_CONTENT = b"Test ISO content for upload\n" * 100
TEST_SHA256 = hashlib.sha256(TEST_CONTENT).hexdigest()

CREATE_RESPONSE = '<?xml version="1.0"?><feed><entry /></feed>'
IMPORT_RESPONSE = '<?xml version="1.0"?><feed><entry /></feed>'

VG_PATH = f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup/{VG_UUID}"


def _media_feed(media_name: str | None = None, media_size: int = 0) -> str:
    """Build a VirtualMediaRepository feed response."""
    if media_name is None:
        return """<?xml version="1.0" encoding="UTF-8"?>
<feed>
  <entry>
    <content>
      <VirtualIOServer>
        <VolumeGroup UUID="{vg}">
          <VirtualMediaRepository>
            <VMLibrary />
          </VirtualMediaRepository>
        </VolumeGroup>
      </VirtualIOServer>
    </content>
  </entry>
</feed>""".format(vg=VG_UUID)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed>
  <entry>
    <content>
      <VirtualIOServer>
        <VolumeGroup UUID="{VG_UUID}">
          <VirtualMediaRepository>
            <VMLibrary>
              <VirtualOpticalMedia>
                <MediaName>{media_name}</MediaName>
                <MediaSize>{media_size}</MediaSize>
                <MediaType>ISO</MediaType>
              </VirtualOpticalMedia>
            </VMLibrary>
          </VirtualMediaRepository>
        </VolumeGroup>
      </VirtualIOServer>
    </content>
  </entry>
</feed>"""


@pytest.fixture
def mock_iso_file(tmp_path: Path):
    """Create a temporary ISO file for testing."""
    iso_file = tmp_path / "test.iso"
    iso_file.write_bytes(TEST_CONTENT)
    return iso_file


@pytest.mark.asyncio
async def test_upload_iso_success(mock_hmc, mock_iso_file):
    """Upload ISO succeeds with all broker operations and cleanup."""
    broker_uri = "https://hmc.test:12443/rest/api/uom/BrokeredFile/broker-123"

    # create + import both POST to same URL — use side_effect for sequential responses
    mock_hmc.post(VG_PATH).mock(
        side_effect=[
            httpx.Response(201, text=CREATE_RESPONSE, headers={"Location": broker_uri}),
            httpx.Response(200, text=IMPORT_RESPONSE),
        ]
    )
    mock_hmc.put(broker_uri).mock(return_value=httpx.Response(200, text=""))
    mock_hmc.get(VG_PATH).mock(
        return_value=httpx.Response(200, text=_media_feed(MEDIA_NAME, len(TEST_CONTENT)))
    )
    mock_hmc.delete(broker_uri).mock(return_value=httpx.Response(204, text=""))

    config = make_config()
    async with HMCClient(config) as hmc:
        hmc.list_optical_media = AsyncMock(
            side_effect=[
                [],  # first call: empty repo (no collision)
                [{"MediaName": MEDIA_NAME, "MediaSize": len(TEST_CONTENT)}],  # after import
            ]
        )
        result = await upload_iso(hmc, VIOS_UUID, VG_UUID, MEDIA_NAME, mock_iso_file)

    assert result["status"] == "uploaded"
    assert result["media_name"] == MEDIA_NAME
    assert result["media_size_bytes"] == len(TEST_CONTENT)
    assert result["sha256"] == TEST_SHA256
    assert result["media"]["MediaName"] == MEDIA_NAME
    assert result["existing_name"] is None


@pytest.mark.asyncio
async def test_upload_iso_name_collision(mock_hmc, mock_iso_file):
    """Upload ISO fails when media name already exists in repository."""
    config = make_config()
    async with HMCClient(config) as hmc:
        hmc.list_optical_media = AsyncMock(
            return_value=[{"MediaName": MEDIA_NAME, "MediaSize": 100}]
        )

        with pytest.raises(FileExistsError) as exc_info:
            await upload_iso(hmc, VIOS_UUID, VG_UUID, MEDIA_NAME, mock_iso_file)

        assert f"Media name '{MEDIA_NAME}' already exists" in str(exc_info.value)


@pytest.mark.asyncio
async def test_upload_iso_file_not_found(mock_hmc):
    """Upload ISO fails when local file does not exist."""
    config = make_config()
    async with HMCClient(config) as hmc:
        hmc.list_optical_media = AsyncMock(return_value=[])

        with pytest.raises(ValueError) as exc_info:
            await upload_iso(hmc, VIOS_UUID, VG_UUID, MEDIA_NAME, "/nonexistent.iso")

        assert "ISO file does not exist" in str(exc_info.value)


@pytest.mark.asyncio
async def test_upload_iso_file_not_readable(mock_hmc, tmp_path: Path):
    """Upload ISO fails when local file is not readable."""
    iso_file = tmp_path / "unreadable.iso"
    iso_file.write_bytes(TEST_CONTENT)
    iso_file.chmod(0o000)

    config = make_config()
    async with HMCClient(config) as hmc:
        hmc.list_optical_media = AsyncMock(return_value=[])

        with pytest.raises(ValueError) as exc_info:
            await upload_iso(hmc, VIOS_UUID, VG_UUID, MEDIA_NAME, iso_file)

        assert "not readable" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_upload_iso_broker_cleanup_on_error(mock_hmc, mock_iso_file):
    """Upload ISO cleans up broker resources when import fails."""
    broker_uri = "https://hmc.test:12443/rest/api/uom/BrokeredFile/broker-error"

    mock_hmc.post(VG_PATH).mock(
        side_effect=[
            httpx.Response(201, text=CREATE_RESPONSE, headers={"Location": broker_uri}),
            httpx.Response(500, text="Import failed"),
        ]
    )
    mock_hmc.put(broker_uri).mock(return_value=httpx.Response(200, text=""))
    mock_hmc.delete(broker_uri).mock(return_value=httpx.Response(204, text=""))

    config = make_config()
    async with HMCClient(config) as hmc:
        hmc.list_optical_media = AsyncMock(return_value=[])

        with pytest.raises(HMCError):
            await upload_iso(hmc, VIOS_UUID, VG_UUID, MEDIA_NAME, mock_iso_file)


@pytest.mark.asyncio
async def test_upload_iso_empty_repository(mock_hmc, mock_iso_file):
    """Upload ISO succeeds when repository is empty (media not found after import)."""
    broker_uri = "https://hmc.test:12443/rest/api/uom/BrokeredFile/broker-empty"

    mock_hmc.post(VG_PATH).mock(
        side_effect=[
            httpx.Response(201, text=CREATE_RESPONSE, headers={"Location": broker_uri}),
            httpx.Response(200, text=IMPORT_RESPONSE),
        ]
    )
    mock_hmc.put(broker_uri).mock(return_value=httpx.Response(200, text=""))
    mock_hmc.get(VG_PATH).mock(return_value=httpx.Response(200, text=_media_feed()))
    mock_hmc.delete(broker_uri).mock(return_value=httpx.Response(204, text=""))

    config = make_config()
    async with HMCClient(config) as hmc:
        hmc.list_optical_media = AsyncMock(return_value=[])
        result = await upload_iso(hmc, VIOS_UUID, VG_UUID, MEDIA_NAME, mock_iso_file)

    assert result["status"] == "uploaded"
    assert result["media"] is None
    assert result["sha256"] == TEST_SHA256


@pytest.mark.asyncio
async def test_upload_iso_broker_create_missing_location(mock_hmc, mock_iso_file):
    """Upload ISO fails when broker create doesn't return Location header."""
    # _broker_file_create raises HMCError when Location header is missing.
    config = make_config()
    async with HMCClient(config) as hmc:
        hmc.list_optical_media = AsyncMock(return_value=[])
        hmc._broker_file_create = AsyncMock(
            side_effect=HMCError("Brokered file create missing Location header", 200, "")
        )

        with pytest.raises(HMCError):
            await upload_iso(hmc, VIOS_UUID, VG_UUID, MEDIA_NAME, mock_iso_file)


@pytest.mark.asyncio
async def test_upload_iso_large_file(mock_hmc, tmp_path: Path):
    """Upload ISO computes SHA-256 correctly for large file."""
    large_content = b"X" * (1024 * 1024)
    large_file = tmp_path / "large.iso"
    large_file.write_bytes(large_content)
    large_sha256 = hashlib.sha256(large_content).hexdigest()

    broker_uri = "https://hmc.test:12443/rest/api/uom/BrokeredFile/broker-large"

    mock_hmc.post(VG_PATH).mock(
        side_effect=[
            httpx.Response(201, text=CREATE_RESPONSE, headers={"Location": broker_uri}),
            httpx.Response(200, text=IMPORT_RESPONSE),
        ]
    )
    mock_hmc.put(broker_uri).mock(return_value=httpx.Response(200, text=""))
    mock_hmc.get(VG_PATH).mock(
        return_value=httpx.Response(200, text=_media_feed(MEDIA_NAME, len(large_content)))
    )
    mock_hmc.delete(broker_uri).mock(return_value=httpx.Response(204, text=""))

    config = make_config()
    async with HMCClient(config) as hmc:
        hmc.list_optical_media = AsyncMock(return_value=[])
        result = await upload_iso(hmc, VIOS_UUID, VG_UUID, MEDIA_NAME, large_file)

    assert result["status"] == "uploaded"
    assert result["media_size_bytes"] == len(large_content)
    assert result["sha256"] == large_sha256
