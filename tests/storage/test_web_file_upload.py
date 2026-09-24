"""Transport pins for the HMC web File requests that carry an ISO upload (ADR 0177).

The create response fixture copies the shape the V10R3 M1060 HMC returned on
2026-09-23: an Atom entry whose content holds ``File:File`` with the web
namespace as its default namespace.
"""

import functools
from collections.abc import AsyncIterator

import httpx
import pytest
from conftest import make_config
from defusedxml import ElementTree as DET

from hmcpctl.client.core import HMCClient
from hmcpctl.errors import HMCError
from hmcpctl.xmlutil import WEB_NS, localname

VIOS_UUID = "00000000-0000-0000-0000-000000000001"
FILE_UUID = "33333333-3333-3333-3333-333333330003"
MEDIA_NAME = "test_image.iso"
TEST_UPLOAD_CONTENT = b"Fake ISO content for testing the web File upload.\n" * 100

FILE_PATH = "/rest/api/web/File"
CONTENTS_PATH = f"/rest/api/web/File/contents/{FILE_UUID}"
DELETE_PATH = f"/rest/api/web/File/{FILE_UUID}"

# An ISO name an operator could plausibly type, carrying all five XML
# metacharacters at once (#284).
METACHARACTER_MEDIA_NAME = "R&D <a> \"b\" 'c'.iso"


def file_response(*file_uuids: str) -> str:
    """The create response, carrying one ``FileUUID`` element per argument."""
    uuids = "".join(
        f'<FileUUID kxe="false" kb="ROR">{file_uuid}</FileUUID>' for file_uuid in file_uuids
    )
    return f"""<entry xmlns="http://www.w3.org/2005/Atom">
    <id>{FILE_UUID}</id>
    <title>File</title>
    <content type="application/vnd.ibm.powervm.web+xml; type=File">
        <File:File xmlns:File="{WEB_NS}" xmlns="{WEB_NS}" schemaVersion="V1_0">
    <Metadata><Atom><AtomID>{FILE_UUID}</AtomID></Atom></Metadata>
    <Filename kxe="false" kb="COR">{MEDIA_NAME}</Filename>
    <DateModified kxe="false" kb="ROR">1790222889325</DateModified>
    <InternetMediaType kxe="false" kb="COR">application/octet-stream</InternetMediaType>
    {uuids}
    <ExpectedFileSizeInBytes kxe="false" kb="COD">4800</ExpectedFileSizeInBytes>
    <FileEnumType kxe="false" kb="COR">BROKERED_MEDIA_ISO</FileEnumType>
    <TargetVirtualIOServerUUID kb="COR" kxe="false">{VIOS_UUID}</TargetVirtualIOServerUUID>
</File:File>
    </content>
</entry>
"""


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

    def __init__(self) -> None:
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
        return httpx.Response(204)


@pytest.mark.asyncio
async def test_web_file_create_sends_the_proven_request(mock_hmc):
    """The create request is the one HMC V10R3 M1060 accepted (ADR 0177)."""
    route = mock_hmc.put(FILE_PATH).mock(
        return_value=httpx.Response(200, text=file_response(FILE_UUID))
    )

    async with HMCClient(make_config(schema_version="")) as hmc:
        result = await hmc._web_file_create(VIOS_UUID, MEDIA_NAME, 4800)

    assert result == FILE_UUID
    request = route.calls.last.request
    assert request.method == "PUT"
    assert request.headers["Content-Type"] == "application/vnd.ibm.powervm.web+xml; type=File"
    assert request.headers["Accept"] == "*/*"
    assert "X-HMC-Schema-Version" not in request.headers
    root = DET.fromstring(request.content)
    assert root.tag == f"{{{WEB_NS}}}File"
    assert root.get("schemaVersion") == "V1_0"
    children = [(localname(child.tag), child.text) for child in root]
    assert children == [
        ("Metadata", None),
        ("Filename", MEDIA_NAME),
        ("InternetMediaType", "application/octet-stream"),
        ("ExpectedFileSizeInBytes", "4800"),
        ("FileEnumType", "BROKERED_MEDIA_ISO"),
        ("TargetVirtualIOServerUUID", VIOS_UUID),
    ]


@pytest.mark.asyncio
async def test_web_file_create_carries_a_configured_schema_version(mock_hmc):
    """The File requests add the optional header the way every web request does."""
    route = mock_hmc.put(FILE_PATH).mock(
        return_value=httpx.Response(200, text=file_response(FILE_UUID))
    )

    async with HMCClient(make_config(schema_version="V1_0")) as hmc:
        await hmc._web_file_create(VIOS_UUID, MEDIA_NAME, 1)

    assert route.calls.last.request.headers["X-HMC-Schema-Version"] == "V1_0"


@pytest.mark.parametrize(
    "body",
    [
        file_response(),
        file_response(FILE_UUID, FILE_UUID),
        file_response("not-a-uuid"),
        file_response(f"{FILE_UUID}/../x"),
        "not xml",
        '<!DOCTYPE a [<!ENTITY e "x">]><a>&e;</a>',
    ],
    ids=["none", "two", "not-uuid", "traversal", "not-xml", "entity"],
)
@pytest.mark.asyncio
async def test_web_file_create_rejects_a_response_without_one_file_uuid(mock_hmc, body):
    """Only one UUID-shaped FileUUID can address the later requests."""
    mock_hmc.put(FILE_PATH).mock(return_value=httpx.Response(200, text=body))

    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError, match="Web File create response"):
            await hmc._web_file_create(VIOS_UUID, MEDIA_NAME, 1)


@pytest.mark.asyncio
async def test_web_file_create_raises_on_failure_status(mock_hmc):
    mock_hmc.put(FILE_PATH).mock(return_value=httpx.Response(400, text="REST0001"))

    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError, match="Web File create failed") as raised:
            await hmc._web_file_create(VIOS_UUID, MEDIA_NAME, 1)

    assert raised.value.status_code == 400


@pytest.mark.asyncio
async def test_web_file_create_refuses_a_non_uuid_vios_before_transport(mock_hmc):
    route = mock_hmc.put(FILE_PATH)

    async with HMCClient(make_config()) as hmc:
        with pytest.raises(ValueError, match="vios_uuid must be a UUID"):
            await hmc._web_file_create("../vios", MEDIA_NAME, 1)

    assert not route.called


@pytest.mark.asyncio
async def test_web_file_create_round_trips_metacharacters(mock_hmc):
    """A media name carrying all five metacharacters reaches the HMC intact."""
    route = mock_hmc.put(FILE_PATH).mock(
        return_value=httpx.Response(200, text=file_response(FILE_UUID))
    )

    async with HMCClient(make_config()) as hmc:
        await hmc._web_file_create(VIOS_UUID, METACHARACTER_MEDIA_NAME, 1)

    body = route.calls.last.request.content.decode()
    assert _texts(body, "Filename") == [METACHARACTER_MEDIA_NAME]


@pytest.mark.asyncio
async def test_web_file_create_rejects_an_unrepresentable_filename(mock_hmc):
    """A character XML 1.0 cannot carry is refused before the request is sent."""
    route = mock_hmc.put(FILE_PATH)

    async with HMCClient(make_config()) as hmc:
        with pytest.raises(ValueError, match=r"U\+0000"):
            await hmc._web_file_create(VIOS_UUID, "a\x00.iso", 1)

    assert not route.called


@pytest.mark.asyncio
async def test_web_file_upload_streams_to_the_contents_path(mock_hmc):
    route = mock_hmc.put(CONTENTS_PATH).mock(return_value=httpx.Response(204))

    async with HMCClient(make_config()) as hmc:
        result = await hmc._web_file_upload(
            FILE_UUID, _aiter(TEST_UPLOAD_CONTENT), len(TEST_UPLOAD_CONTENT)
        )

    assert result is None
    request = route.calls.last.request
    # respx materialized the stream before recording the call, so `.content` is
    # the reassembled body — what the HMC receives, not evidence of its shape.
    # `test_web_file_upload_sends_a_stream_the_body_never_buffers` owns that.
    assert request.content == TEST_UPLOAD_CONTENT
    assert request.headers["Content-Type"] == "application/octet-stream"
    assert request.headers["Content-Length"] == str(len(TEST_UPLOAD_CONTENT))
    assert request.headers["Accept"] == "*/*"


@pytest.mark.asyncio
async def test_web_file_upload_sends_a_stream_the_body_never_buffers(monkeypatch):
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
    transport = _StreamShapeTransport()
    monkeypatch.setattr(
        "hmcpctl.client.core.httpx.AsyncClient",
        functools.partial(httpx.AsyncClient, transport=transport),
    )

    # No `async with`: logon would run through the same transport, and this
    # primitive does not depend on the session it would establish.
    hmc = HMCClient(make_config())
    try:
        await hmc._web_file_upload(FILE_UUID, _aiter(*chunks), len(TEST_UPLOAD_CONTENT))
    finally:
        await hmc._http.aclose()

    assert transport.stream_is_async_only is True
    assert transport.chunks == chunks
    assert b"".join(transport.chunks) == TEST_UPLOAD_CONTENT
    assert transport.headers["Content-Length"] == str(
        sum(len(chunk) for chunk in transport.chunks)
    )
    assert "Transfer-Encoding" not in transport.headers


@pytest.mark.asyncio
async def test_web_file_upload_raises_on_failure_status(mock_hmc):
    mock_hmc.put(CONTENTS_PATH).mock(return_value=httpx.Response(500, text="failed"))

    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError, match="Web File upload failed") as raised:
            await hmc._web_file_upload(FILE_UUID, _aiter(b"iso"), 3)

    assert raised.value.status_code == 500


@pytest.mark.asyncio
async def test_web_file_delete_addresses_the_file(mock_hmc):
    route = mock_hmc.delete(DELETE_PATH).mock(return_value=httpx.Response(204))

    async with HMCClient(make_config()) as hmc:
        await hmc._web_file_delete(FILE_UUID)

    assert route.calls.last.request.headers["Accept"] == "*/*"


@pytest.mark.parametrize("status", [200, 202, 204, 404])
@pytest.mark.asyncio
async def test_web_file_delete_tolerates_success_and_absence(mock_hmc, status):
    """A File already gone (404) is as released as one this call deleted."""
    route = mock_hmc.delete(DELETE_PATH).mock(return_value=httpx.Response(status))

    async with HMCClient(make_config()) as hmc:
        await hmc._web_file_delete(FILE_UUID)

    assert route.called


@pytest.mark.asyncio
async def test_web_file_delete_raises_on_failure_status(mock_hmc):
    mock_hmc.delete(DELETE_PATH).mock(return_value=httpx.Response(500, text="failed"))

    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError, match="Web File delete failed"):
            await hmc._web_file_delete(FILE_UUID)
