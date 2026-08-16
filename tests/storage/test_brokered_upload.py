"""Deterministic transport fixtures for brokered file upload/import verification.

These fixtures exercise the complete brokered upload/import sequence at the
transport boundary and record endpoint paths, media types, request/response
documents, cleanup behavior, and version-dependent failures.

All fixtures are designed to be versioned and deterministic, capturing the
exact HTTP exchanges needed to understand HMC/VIOS behavior without exposing
a speculative public API.
"""

import httpx
import pytest

from conftest import make_config

from hmc_mcp.client import HMCClient

# --------------------------------------------------------------------------- #
# Brokered file creation fixtures
# --------------------------------------------------------------------------- #

BROKERED_FILE_CREATE_REQUEST = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<BrokeredFile xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
  <Filename>test-image.iso</Filename>
</BrokeredFile>
"""

BROKERED_FILE_CREATE_RESPONSE_201 = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:broker-file-uuid-001</id>
  <title>BrokeredFile:test-image.iso</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <BrokeredFile xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <Filename>test-image.iso</Filename>
      <BrokeredFileURI>https://hmc.test:12443/rest/api/uom/BrokeredFile/broker-file-uuid-001</BrokeredFileURI>
    </BrokeredFile>
  </content>
</entry>
"""

# --------------------------------------------------------------------------- #
# Brokered file upload fixtures
# --------------------------------------------------------------------------- #

# Small test content for upload (not a real ISO)
TEST_UPLOAD_CONTENT = b"Fake ISO content for testing brokered upload.\n" * 100

BROKERED_FILE_UPLOAD_RESPONSE_200 = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:media-uuid-002</id>
  <title>VirtualOpticalMedia:test-image.iso</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <VirtualOpticalMedia xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <MediaName>test-image.iso</MediaName>
      <MediaSize>4500</MediaSize>
      <MediaType>ISO9660</MediaType>
    </VirtualOpticalMedia>
  </content>
</entry>
"""

# --------------------------------------------------------------------------- #
# ISO import fixtures
# --------------------------------------------------------------------------- #

BROKERED_ISO_IMPORT_REQUEST = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<LinkedVirtualOpticalMedia xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
  <MediaName>test-image.iso</MediaName>
  <LinkedFileURI>https://hmc.test:12443/rest/api/uom/BrokeredFile/broker-file-uuid-001</LinkedFileURI>
</LinkedVirtualOpticalMedia>
"""

BROKERED_ISO_IMPORT_RESPONSE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:imported-media-uuid-003</id>
    <title>VirtualOpticalMedia:test-image.iso</title>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <VirtualOpticalMedia xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <MediaName>test-image.iso</MediaName>
        <MediaSize>4500</MediaSize>
        <MediaType>ISO9660</MediaType>
        <!-- Note: Checksum fields are deliberately absent to verify whether HMC exposes them -->
      </VirtualOpticalMedia>
    </content>
  </entry>
</feed>
"""

# --------------------------------------------------------------------------- #
# Cleanup fixtures
# --------------------------------------------------------------------------- #

# Cleanup may return 204 No Content or 404 if already deleted
BROKERED_FILE_DELETE_RESPONSE_204 = ""

# --------------------------------------------------------------------------- #
# Checksum query fixtures
# --------------------------------------------------------------------------- #

# VirtualOpticalMedia listing that may or may not contain checksum information
MEDIA_LIST_WITHOUT_CHECKSUM = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:imported-media-uuid-003</id>
    <title>VirtualOpticalMedia:test-image.iso</title>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <VirtualOpticalMedia xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <MediaName>test-image.iso</MediaName>
        <MediaSize>4500</MediaSize>
        <MediaType>ISO9660</MediaType>
      </VirtualOpticalMedia>
    </content>
  </entry>
</feed>
"""

# Version-dependent checksum response (hypothetical - to be verified)
MEDIA_LIST_WITH_CHECKSUM = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:imported-media-uuid-003</id>
    <title>VirtualOpticalMedia:test-image.iso</title>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <VirtualOpticalMedia xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <MediaName>test-image.iso</MediaName>
        <MediaSize>4500</MediaSize>
        <MediaType>ISO9660</MediaType>
        <ChecksumType>SHA-256</ChecksumType>
        <ChecksumValue>abc123def456...</ChecksumValue>
      </VirtualOpticalMedia>
    </content>
  </entry>
</feed>
"""

# --------------------------------------------------------------------------- #
# Test constants
# --------------------------------------------------------------------------- #

VIOS_UUID = "00000000-0000-0000-0000-000000000001"
VG_UUID = "00000000-0000-0000-0000-000000000002"
MEDIA_NAME = "test-image.iso"
BROKER_URI = "https://hmc.test:12443/rest/api/uom/BrokeredFile/broker-file-uuid-001"


# --------------------------------------------------------------------------- #
# Transport primitive tests
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_broker_file_create_success(mock_hmc):
    """Brokered file create returns Location header with broker URI."""
    create_route = mock_hmc.post(
        "/rest/api/uom/VirtualIOServer/00000000-0000-0000-0000-000000000001/VolumeGroup/00000000-0000-0000-0000-000000000002"
    ).mock(
        return_value=httpx.Response(
            201,
            text=BROKERED_FILE_CREATE_RESPONSE_201,
            headers={"Location": BROKER_URI},
        )
    )

    async with HMCClient(make_config()) as hmc:
        result = await hmc._broker_file_create(VIOS_UUID, VG_UUID, MEDIA_NAME)

    assert create_route.called
    request = create_route.calls.last.request
    assert request.content.decode() == BROKERED_FILE_CREATE_REQUEST
    assert result == BROKER_URI


@pytest.mark.asyncio
async def test_broker_file_create_missing_location_header(mock_hmc):
    """Brokered file create raises when Location header is absent."""
    mock_hmc.post(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup/{VG_UUID}"
    ).mock(
        return_value=httpx.Response(
            201,
            text=BROKERED_FILE_CREATE_RESPONSE_201,
            headers={},  # No Location header
        )
    )

    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError, match="missing Location header"):
            await hmc._broker_file_create(VIOS_UUID, VG_UUID, MEDIA_NAME)


@pytest.mark.asyncio
async def test_broker_file_upload_success(mock_hmc):
    """Brokered file upload streams content and returns response text."""
    upload_route = mock_hmc.put(BROKER_URI).mock(
        return_value=httpx.Response(200, text=BROKERED_FILE_UPLOAD_RESPONSE_200)
    )

    async with HMCClient(make_config()) as hmc:
        result = await hmc._broker_file_upload(BROKER_URI, TEST_UPLOAD_CONTENT)

    assert upload_route.called
    request = upload_route.calls.last.request
    assert request.content == TEST_UPLOAD_CONTENT
    assert request.headers["Content-Type"] == "application/octet-stream"
    assert request.headers["Content-Length"] == str(len(TEST_UPLOAD_CONTENT))
    assert result == BROKERED_FILE_UPLOAD_RESPONSE_200


@pytest.mark.asyncio
async def test_broker_iso_import_success(mock_hmc):
    """Brokered ISO import creates VirtualOpticalMedia linked to brokered file."""
    import_route = mock_hmc.post(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup/{VG_UUID}"
    ).mock(return_value=httpx.Response(200, text=BROKERED_ISO_IMPORT_RESPONSE))

    async with HMCClient(make_config()) as hmc:
        result = await hmc._broker_iso_import(VIOS_UUID, VG_UUID, MEDIA_NAME, BROKER_URI)

    assert import_route.called
    request = import_route.calls.last.request
    assert BROKERED_ISO_IMPORT_REQUEST in request.content.decode()
    assert "LinkedFileURI" in request.content.decode()
    assert BROKER_URI in request.content.decode()
    assert result == BROKERED_ISO_IMPORT_RESPONSE


@pytest.mark.asyncio
async def test_broker_file_cleanup_success(mock_hmc):
    """Brokered file cleanup accepts 200, 202, 204, and tolerates 404."""
    # Test 204 No Content
    cleanup_route_204 = mock_hmc.delete(BROKER_URI).mock(
        return_value=httpx.Response(204, text="")
    )

    async with HMCClient(make_config()) as hmc:
        await hmc._broker_file_cleanup(BROKER_URI)

    assert cleanup_route_204.called

    # Test 404 (already deleted) - should not raise
    cleanup_route_404 = mock_hmc.delete(BROKER_URI).mock(
        return_value=httpx.Response(404, text="Not found")
    )

    async with HMCClient(make_config()) as hmc:
        await hmc._broker_file_cleanup(BROKER_URI)

    assert cleanup_route_404.called


@pytest.mark.asyncio
async def test_broker_file_cleanup_failure(mock_hmc):
    """Brokered file cleanup raises on unexpected status codes."""
    mock_hmc.delete(BROKER_URI).mock(
        return_value=httpx.Response(500, text="Internal server error")
    )

    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError, match="Brokered file cleanup failed"):
            await hmc._broker_file_cleanup(BROKER_URI)


@pytest.mark.asyncio
async def test_verify_imported_checksum_without_checksum(mock_hmc):
    """Checksum query returns None when HMC does not expose checksum information."""
    mock_hmc.get(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup/{VG_UUID}/VirtualMediaRepository/VMLibrary/VirtualOpticalMedia"
    ).mock(return_value=httpx.Response(200, text=MEDIA_LIST_WITHOUT_CHECKSUM))

    async with HMCClient(make_config()) as hmc:
        result = await hmc._verify_imported_checksum(VIOS_UUID, VG_UUID, MEDIA_NAME)

    assert result is None


@pytest.mark.asyncio
async def test_verify_imported_checksum_empty_response(mock_hmc):
    """Checksum query returns None when endpoint returns empty response."""
    mock_hmc.get(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup/{VG_UUID}/VirtualMediaRepository/VMLibrary/VirtualOpticalMedia"
    ).mock(return_value=httpx.Response(204, text=""))

    async with HMCClient(make_config()) as hmc:
        result = await hmc._verify_imported_checksum(VIOS_UUID, VG_UUID, MEDIA_NAME)

    assert result is None


@pytest.mark.asyncio
async def test_complete_brokered_upload_sequence(mock_hmc):
    """Complete brokered upload/import sequence: create, upload, import, cleanup."""
    # Setup: brokered file create
    create_route = mock_hmc.post(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup/{VG_UUID}"
    ).mock(
        return_value=httpx.Response(
            201,
            text=BROKERED_FILE_CREATE_RESPONSE_201,
            headers={"Location": BROKER_URI},
        )
    )

    # Setup: upload
    upload_route = mock_hmc.put(BROKER_URI).mock(
        return_value=httpx.Response(200, text=BROKERED_FILE_UPLOAD_RESPONSE_200)
    )

    # Setup: import
    import_route = mock_hmc.post(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup/{VG_UUID}"
    ).mock(return_value=httpx.Response(200, text=BROKERED_ISO_IMPORT_RESPONSE))

    # Setup: cleanup
    cleanup_route = mock_hmc.delete(BROKER_URI).mock(
        return_value=httpx.Response(204, text="")
    )

    # Execute the sequence
    async with HMCClient(make_config()) as hmc:
        # Step 1: Create brokered file
        broker_uri = await hmc._broker_file_create(VIOS_UUID, VG_UUID, MEDIA_NAME)
        assert broker_uri == BROKER_URI
        assert create_route.called

        # Step 2: Upload content
        upload_result = await hmc._broker_file_upload(broker_uri, TEST_UPLOAD_CONTENT)
        assert upload_result == BROKERED_FILE_UPLOAD_RESPONSE_200
        assert upload_route.called

        # Step 3: Import into Virtual Media Library
        import_result = await hmc._broker_iso_import(
            VIOS_UUID, VG_UUID, MEDIA_NAME, broker_uri
        )
        assert import_result == BROKERED_ISO_IMPORT_RESPONSE
        assert import_route.called

        # Step 4: Cleanup brokered file
        await hmc._broker_file_cleanup(broker_uri)
        assert cleanup_route.called