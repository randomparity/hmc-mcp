"""Deterministic transport fixtures for brokered file upload/import verification.

These fixtures exercise the complete brokered upload/import sequence at the
transport boundary and record endpoint paths, media types, request/response
documents, cleanup behavior, and version-dependent failures.

All fixtures are designed to be versioned and deterministic, capturing the
exact HTTP exchanges needed to understand HMC/VIOS behavior without exposing
a speculative public API.
"""

import functools
from collections.abc import AsyncIterator

import httpx
import pytest
from defusedxml import ElementTree as DET

from conftest import make_config

from hmc_mcp.client.core import HMCClient
from hmc_mcp.errors import HMCError
from hmc_mcp.xmlutil import localname

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
# Test constants
# --------------------------------------------------------------------------- #

VIOS_UUID = "00000000-0000-0000-0000-000000000001"
VG_UUID = "00000000-0000-0000-0000-000000000002"
MEDIA_NAME = "test-image.iso"
BROKER_URI = "https://hmc.test:12443/rest/api/uom/BrokeredFile/broker-file-uuid-001"
VG_PATH = f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup/{VG_UUID}"

# An ISO name an operator could plausibly type, carrying all five XML
# metacharacters at once (#284).
METACHARACTER_MEDIA_NAME = "R&D <a> \"b\" 'c'.iso"


def _texts(body: str, element: str) -> list[str]:
    """Parsed text of every *element* in a request body, in document order."""
    parsed = DET.fromstring(body.encode("utf-8"))
    return [el.text for el in parsed.iter() if localname(el.tag) == element]


async def _aiter(*chunks: bytes) -> AsyncIterator[bytes]:
    """Yield *chunks* from an async iterator, the body shape the upload takes."""
    for chunk in chunks:
        yield chunk


class _StreamShapeTransport(httpx.AsyncBaseTransport):
    """Record an outgoing body's shape *before* anything materializes it.

    respx — like ``httpx.MockTransport`` — calls ``request.aread()`` before it
    hands the request to a route, which replaces the outgoing stream with an
    already-materialized ``ByteStream``. A test written against a respx route
    therefore cannot tell a streamed body from a slurped one: ``request.content``
    answers the same either way. This transport sits where respx would and reads
    the stream itself, so the shape has something to be asserted against.
    """

    def __init__(self, response_text: str) -> None:
        self._response_text = response_text
        self.stream_is_async_only: bool | None = None
        self.chunks: list[bytes] = []
        self.headers: httpx.Headers | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        # `ByteStream` — what a `bytes` body produces — implements both, so
        # "async and not sync" is what distinguishes a stream from a buffer.
        self.stream_is_async_only = isinstance(
            request.stream, httpx.AsyncByteStream
        ) and not isinstance(request.stream, httpx.SyncByteStream)
        self.headers = request.headers
        self.chunks = [chunk async for chunk in request.stream]
        return httpx.Response(200, text=self._response_text)


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
    """Brokered file upload sends the streamed content and returns response text."""
    upload_route = mock_hmc.put(BROKER_URI).mock(
        return_value=httpx.Response(200, text=BROKERED_FILE_UPLOAD_RESPONSE_200)
    )

    async with HMCClient(make_config()) as hmc:
        result = await hmc._broker_file_upload(
            BROKER_URI, _aiter(TEST_UPLOAD_CONTENT), len(TEST_UPLOAD_CONTENT)
        )

    assert upload_route.called
    request = upload_route.calls.last.request
    # respx materialized the stream before recording the call, so `.content` is
    # the reassembled body — what the HMC receives, not evidence of its shape.
    # `test_broker_file_upload_sends_a_stream_the_body_never_buffers` owns that.
    assert request.content == TEST_UPLOAD_CONTENT
    assert request.headers["Content-Type"] == "application/octet-stream"
    assert request.headers["Content-Length"] == str(len(TEST_UPLOAD_CONTENT))
    assert result == BROKERED_FILE_UPLOAD_RESPONSE_200


@pytest.mark.asyncio
async def test_broker_file_upload_sends_a_stream_the_body_never_buffers(monkeypatch):
    """#308: the ISO leaves the process chunk by chunk, under a declared length.

    Three properties, each of which a whole-file `content=bytes` upload breaks:
    the outgoing body is an async-only stream; the chunks the caller yielded
    arrive as separate chunks rather than one buffer; and the `Content-Length`
    the HMC is promised equals the number of bytes that actually go out. The
    third is what makes the second safe — an explicit `Content-Length` is also
    why httpx does not fall back to `Transfer-Encoding: chunked` here.
    """
    chunks = [TEST_UPLOAD_CONTENT[:1000], TEST_UPLOAD_CONTENT[1000:3000],
              TEST_UPLOAD_CONTENT[3000:]]
    transport = _StreamShapeTransport(BROKERED_FILE_UPLOAD_RESPONSE_200)
    monkeypatch.setattr(
        "hmc_mcp.client.core.httpx.AsyncClient",
        functools.partial(httpx.AsyncClient, transport=transport),
    )

    # No `async with`: logon would run through the same transport, and this
    # primitive does not depend on the session it would establish.
    hmc = HMCClient(make_config())
    try:
        result = await hmc._broker_file_upload(
            BROKER_URI, _aiter(*chunks), len(TEST_UPLOAD_CONTENT)
        )
    finally:
        await hmc._http.aclose()

    assert transport.stream_is_async_only is True
    assert transport.chunks == chunks
    assert b"".join(transport.chunks) == TEST_UPLOAD_CONTENT
    assert transport.headers["Content-Length"] == str(
        sum(len(chunk) for chunk in transport.chunks)
    )
    assert "Transfer-Encoding" not in transport.headers
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
async def test_broker_file_create_round_trips_metacharacters(mock_hmc):
    """A media name carrying all five metacharacters reaches the HMC intact."""
    create_route = mock_hmc.post(VG_PATH).mock(
        return_value=httpx.Response(
            201,
            text=BROKERED_FILE_CREATE_RESPONSE_201,
            headers={"Location": BROKER_URI},
        )
    )

    async with HMCClient(make_config()) as hmc:
        await hmc._broker_file_create(VIOS_UUID, VG_UUID, METACHARACTER_MEDIA_NAME)

    body = create_route.calls.last.request.content.decode()
    assert _texts(body, "Filename") == [METACHARACTER_MEDIA_NAME]


@pytest.mark.asyncio
async def test_broker_iso_import_round_trips_metacharacters(mock_hmc):
    """Both interpolated values parse back exactly out of the sent body."""
    import_route = mock_hmc.post(VG_PATH).mock(
        return_value=httpx.Response(200, text=BROKERED_ISO_IMPORT_RESPONSE)
    )
    tainted_uri = f"{BROKER_URI}?a=1&b=2"

    async with HMCClient(make_config()) as hmc:
        await hmc._broker_iso_import(
            VIOS_UUID, VG_UUID, METACHARACTER_MEDIA_NAME, tainted_uri
        )

    body = import_route.calls.last.request.content.decode()
    assert _texts(body, "MediaName") == [METACHARACTER_MEDIA_NAME]
    assert _texts(body, "LinkedFileURI") == [tainted_uri]


@pytest.mark.asyncio
async def test_broker_iso_import_media_name_cannot_redirect_the_broker_uri(mock_hmc):
    """Unescaped, this named a second LinkedFileURI in a well-formed document."""
    import_route = mock_hmc.post(VG_PATH).mock(
        return_value=httpx.Response(200, text=BROKERED_ISO_IMPORT_RESPONSE)
    )

    async with HMCClient(make_config()) as hmc:
        await hmc._broker_iso_import(
            VIOS_UUID,
            VG_UUID,
            "a.iso</MediaName><LinkedFileURI>https://evil.test/x<MediaName>",
            BROKER_URI,
        )

    body = import_route.calls.last.request.content.decode()
    assert _texts(body, "LinkedFileURI") == [BROKER_URI]


@pytest.mark.asyncio
async def test_broker_file_create_rejects_an_unrepresentable_filename(mock_hmc):
    """A character XML 1.0 cannot carry is refused before the request is sent."""
    create_route = mock_hmc.post(VG_PATH)

    async with HMCClient(make_config()) as hmc:
        with pytest.raises(ValueError, match=r"U\+0000"):
            await hmc._broker_file_create(VIOS_UUID, VG_UUID, "a\x00.iso")

    assert not create_route.called


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
async def test_complete_brokered_upload_sequence(mock_hmc):
    """Complete brokered upload/import sequence: create, upload, import, cleanup."""
    # Setup: brokered file create then import (same endpoint, sequential responses)
    post_route = mock_hmc.post(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup/{VG_UUID}"
    ).mock(
        side_effect=[
            httpx.Response(
                201,
                text=BROKERED_FILE_CREATE_RESPONSE_201,
                headers={"Location": BROKER_URI},
            ),
            httpx.Response(200, text=BROKERED_ISO_IMPORT_RESPONSE),
        ]
    )

    # Setup: upload
    upload_route = mock_hmc.put(BROKER_URI).mock(
        return_value=httpx.Response(200, text=BROKERED_FILE_UPLOAD_RESPONSE_200)
    )

    # Setup: cleanup
    cleanup_route = mock_hmc.delete(BROKER_URI).mock(
        return_value=httpx.Response(204, text="")
    )

    # Execute the sequence
    async with HMCClient(make_config()) as hmc:
        # Step 1: Create brokered file
        broker_uri = await hmc._broker_file_create(VIOS_UUID, VG_UUID, MEDIA_NAME)
        assert broker_uri == BROKER_URI

        # Step 2: Upload content
        upload_result = await hmc._broker_file_upload(
            broker_uri, _aiter(TEST_UPLOAD_CONTENT), len(TEST_UPLOAD_CONTENT)
        )
        assert upload_result == BROKERED_FILE_UPLOAD_RESPONSE_200
        assert upload_route.called

        # Step 3: Import into Virtual Media Library
        import_result = await hmc._broker_iso_import(
            VIOS_UUID, VG_UUID, MEDIA_NAME, broker_uri
        )
        assert import_result == BROKERED_ISO_IMPORT_RESPONSE
        assert post_route.called

        # Step 4: Cleanup brokered file
        await hmc._broker_file_cleanup(broker_uri)
        assert cleanup_route.called
