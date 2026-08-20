"""Tests for ISO upload operation via HMC file broker."""

import functools
import hashlib
import socket
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from conftest import make_config

from hmc_mcp.client import HMCClient
from hmc_mcp.config import parse_iso_url_allowlist
from hmc_mcp.errors import HMCError
from hmc_mcp.operations_storage import upload_iso, _download_iso_from_url

# Test constants
VIOS_UUID = "00000000-0000-0000-0000-000000000003"
VG_UUID = "vg-uuid-002"
MEDIA_NAME = "test-image.iso"
ISO_HOST = "images.test"
ISO_URL = f"https://{ISO_HOST}/test-image.iso"
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
def stage_download(tmp_path: Path, monkeypatch):
    """Stub the HTTP download stage and hand back the mock that replaced it.

    ``upload_iso``'s only source is an http(s) URL, so the download is the
    boundary these broker tests mock. The stub stages real bytes on disk because
    the broker upload reads the staged file back and the ``finally`` arm unlinks
    it — behaviour a pure return value would not exercise.
    """

    def _stage(content: bytes = TEST_CONTENT) -> AsyncMock:
        staged = tmp_path / "staged.iso"
        staged.write_bytes(content)
        download = AsyncMock(
            return_value=(staged, hashlib.sha256(content).hexdigest(), len(content))
        )
        monkeypatch.setattr(
            "hmc_mcp.operations_storage._download_iso_from_url", download
        )
        return download

    return _stage


@pytest.mark.asyncio
async def test_upload_iso_success(mock_hmc, stage_download):
    """Upload ISO succeeds with all broker operations and cleanup."""
    broker_uri = "https://hmc.test:12443/rest/api/uom/BrokeredFile/broker-123"
    download = stage_download()

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

    config = make_config(iso_url_allowlist=ISO_HOST)
    async with HMCClient(config) as hmc:
        hmc.list_optical_media = AsyncMock(
            side_effect=[
                [],  # first call: empty repo (no collision)
                [{"MediaName": MEDIA_NAME, "MediaSize": len(TEST_CONTENT)}],  # after import
            ]
        )
        result = await upload_iso(hmc, VIOS_UUID, VG_UUID, MEDIA_NAME, ISO_URL)

    assert result["status"] == "uploaded"
    assert result["media_name"] == MEDIA_NAME
    assert result["media_size_bytes"] == len(TEST_CONTENT)
    assert result["sha256"] == TEST_SHA256
    assert result["media"]["MediaName"] == MEDIA_NAME
    assert result["existing_name"] is None
    download.assert_awaited_once_with(ISO_URL)


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme", ["http", "https", "HTTPS", "Http"])
async def test_upload_iso_accepts_both_supported_schemes(
    mock_hmc, stage_download, scheme
):
    """G261: http and https are the accepted schemes, and each reaches download.

    The mixed-case cases are not padding: `urlparse` normalises the scheme, so a
    later hand-rolled comparison that skipped that step would reject `HTTPS://`
    and break callers while looking like a tightening.
    """
    broker_uri = "https://hmc.test:12443/rest/api/uom/BrokeredFile/broker-scheme"
    download = stage_download()
    url = f"{scheme}://images.test/test-image.iso"

    mock_hmc.post(VG_PATH).mock(
        side_effect=[
            httpx.Response(201, text=CREATE_RESPONSE, headers={"Location": broker_uri}),
            httpx.Response(200, text=IMPORT_RESPONSE),
        ]
    )
    mock_hmc.put(broker_uri).mock(return_value=httpx.Response(200, text=""))
    mock_hmc.delete(broker_uri).mock(return_value=httpx.Response(204, text=""))

    config = make_config(iso_url_allowlist=ISO_HOST)
    async with HMCClient(config) as hmc:
        hmc.list_optical_media = AsyncMock(side_effect=[[], []])
        result = await upload_iso(hmc, VIOS_UUID, VG_UUID, MEDIA_NAME, url)

    assert result["status"] == "uploaded"
    download.assert_awaited_once_with(url)


@pytest.mark.parametrize(
    "rejected",
    [
        "/etc/passwd",
        "/tmp/aix.iso",
        "relative/path.iso",
        "~/Downloads/ubuntu.iso",
        "file:///etc/passwd",
        "ftp://images.test/test.iso",
        "",
    ],
)
@pytest.mark.asyncio
async def test_upload_iso_refuses_every_source_that_is_not_an_http_url(rejected):
    """G261: the local-filesystem source is gone, not merely discouraged.

    Before #261 anything without an http(s) scheme was read as a path on the MCP
    server's own host and uploaded into the granted VIOS's media repository, so a
    caller holding a `mutate` grant for this tool could exfiltrate any file the
    server process could read. The refusal is by scheme, which is why traversal
    and symlink forms need no cases of their own: there is no path branch left
    for them to reach.
    """
    with pytest.raises(ValueError) as exc_info:
        await upload_iso(MagicMock(), VIOS_UUID, VG_UUID, MEDIA_NAME, rejected)

    message = str(exc_info.value)
    assert "http://" in message and "https://" in message
    assert "iso_source" in message


@pytest.mark.asyncio
async def test_upload_iso_refuses_a_local_path_before_touching_anything(
    tmp_path: Path, monkeypatch
):
    """G261: the refusal precedes every filesystem, HMC, and network call.

    A check that stats the path first and refuses afterwards still discloses
    existence and permission through its error text and its timing, so ordering
    is the property under test rather than the refusal alone. Every door out of
    the operation is booby-trapped: `Path` is the module's only filesystem
    access, `resolve_vios_uuid` its first HMC call, and `_download_iso_from_url`
    its only network call.
    """
    # Stands in for the server-side config file the report names; the bytes are
    # inert on purpose, since it is reaching the file at all that is the defect.
    readable = tmp_path / "config.toml"
    readable.write_text("[hmc]\nhost = 'hmc.test'\n", encoding="utf-8")

    def _detonate(name):
        def _boom(*_args, **_kwargs):
            raise AssertionError(f"{name} was reached before the refusal")

        return _boom

    monkeypatch.setattr(
        "hmc_mcp.operations_storage.Path", _detonate("the filesystem")
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_storage.resolve_vios_uuid", _detonate("the HMC")
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_storage._download_iso_from_url", _detonate("the network")
    )

    with pytest.raises(ValueError) as exc_info:
        await upload_iso(MagicMock(), VIOS_UUID, VG_UUID, MEDIA_NAME, str(readable))

    assert "http" in str(exc_info.value)


@pytest.mark.asyncio
async def test_upload_iso_refusal_reveals_nothing_about_the_server_filesystem(
    tmp_path: Path,
):
    """G261: the same refusal for a readable file, an unreadable one, and no file.

    Distinguishable messages would turn the refusal back into the oracle the
    refusal exists to remove.
    """
    readable = tmp_path / "readable.iso"
    readable.write_bytes(TEST_CONTENT)
    unreadable = tmp_path / "unreadable.iso"
    unreadable.write_bytes(TEST_CONTENT)
    unreadable.chmod(0o000)
    absent = tmp_path / "absent.iso"

    messages = set()
    for candidate in (readable, unreadable, absent):
        with pytest.raises(ValueError) as exc_info:
            await upload_iso(
                MagicMock(), VIOS_UUID, VG_UUID, MEDIA_NAME, str(candidate)
            )
        # Only the caller's own input distinguishes the three.
        messages.add(str(exc_info.value).replace(str(candidate), "<source>"))

    assert len(messages) == 1


@pytest.mark.asyncio
async def test_upload_iso_name_collision(mock_hmc, stage_download):
    """Upload ISO fails when media name already exists in repository."""
    stage_download()
    config = make_config(iso_url_allowlist=ISO_HOST)
    async with HMCClient(config) as hmc:
        hmc.list_optical_media = AsyncMock(
            return_value=[{"MediaName": MEDIA_NAME, "MediaSize": 100}]
        )

        with pytest.raises(FileExistsError) as exc_info:
            await upload_iso(hmc, VIOS_UUID, VG_UUID, MEDIA_NAME, ISO_URL)

        assert f"Media name '{MEDIA_NAME}' already exists" in str(exc_info.value)


@pytest.mark.asyncio
async def test_upload_iso_broker_cleanup_on_error(mock_hmc, stage_download):
    """Upload ISO cleans up broker resources when import fails."""
    broker_uri = "https://hmc.test:12443/rest/api/uom/BrokeredFile/broker-error"
    stage_download()

    mock_hmc.post(VG_PATH).mock(
        side_effect=[
            httpx.Response(201, text=CREATE_RESPONSE, headers={"Location": broker_uri}),
            httpx.Response(500, text="Import failed"),
        ]
    )
    mock_hmc.put(broker_uri).mock(return_value=httpx.Response(200, text=""))
    mock_hmc.delete(broker_uri).mock(return_value=httpx.Response(204, text=""))

    config = make_config(iso_url_allowlist=ISO_HOST)
    async with HMCClient(config) as hmc:
        hmc.list_optical_media = AsyncMock(return_value=[])

        with pytest.raises(HMCError):
            await upload_iso(hmc, VIOS_UUID, VG_UUID, MEDIA_NAME, ISO_URL)


@pytest.mark.asyncio
async def test_upload_iso_empty_repository(mock_hmc, stage_download):
    """Upload ISO succeeds when repository is empty (media not found after import)."""
    broker_uri = "https://hmc.test:12443/rest/api/uom/BrokeredFile/broker-empty"
    stage_download()

    mock_hmc.post(VG_PATH).mock(
        side_effect=[
            httpx.Response(201, text=CREATE_RESPONSE, headers={"Location": broker_uri}),
            httpx.Response(200, text=IMPORT_RESPONSE),
        ]
    )
    mock_hmc.put(broker_uri).mock(return_value=httpx.Response(200, text=""))
    mock_hmc.get(VG_PATH).mock(return_value=httpx.Response(200, text=_media_feed()))
    mock_hmc.delete(broker_uri).mock(return_value=httpx.Response(204, text=""))

    config = make_config(iso_url_allowlist=ISO_HOST)
    async with HMCClient(config) as hmc:
        hmc.list_optical_media = AsyncMock(return_value=[])
        result = await upload_iso(hmc, VIOS_UUID, VG_UUID, MEDIA_NAME, ISO_URL)

    assert result["status"] == "uploaded"
    assert result["media"] is None
    assert result["sha256"] == TEST_SHA256


@pytest.mark.asyncio
async def test_upload_iso_broker_create_missing_location(mock_hmc, stage_download):
    """Upload ISO fails when broker create doesn't return Location header."""
    # _broker_file_create raises HMCError when Location header is missing.
    stage_download()
    config = make_config(iso_url_allowlist=ISO_HOST)
    async with HMCClient(config) as hmc:
        hmc.list_optical_media = AsyncMock(return_value=[])
        hmc._broker_file_create = AsyncMock(
            side_effect=HMCError("Brokered file create missing Location header", 200, "")
        )

        with pytest.raises(HMCError):
            await upload_iso(hmc, VIOS_UUID, VG_UUID, MEDIA_NAME, ISO_URL)


@pytest.mark.asyncio
async def test_upload_iso_large_file(mock_hmc, stage_download):
    """Upload ISO reports the SHA-256 and size the download staged."""
    large_content = b"X" * (1024 * 1024)
    large_sha256 = hashlib.sha256(large_content).hexdigest()
    stage_download(large_content)

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

    config = make_config(iso_url_allowlist=ISO_HOST)
    async with HMCClient(config) as hmc:
        hmc.list_optical_media = AsyncMock(return_value=[])
        result = await upload_iso(hmc, VIOS_UUID, VG_UUID, MEDIA_NAME, ISO_URL)

    assert result["status"] == "uploaded"
    assert result["media_size_bytes"] == len(large_content)
    assert result["sha256"] == large_sha256



@pytest.mark.asyncio
async def test_download_iso_from_http_url_success():
    """Download ISO from HTTP URL succeeds with proper streaming and checksum."""
    test_content = b"Test ISO content for HTTP download\n" * 100
    test_url = "http://example.com/test.iso"
    
    # Mock httpx.AsyncClient and response
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    
    # Mock streaming iterator
    async def mock_aiter_bytes(chunk_size=8192):
        for i in range(0, len(test_content), chunk_size):
            yield test_content[i:i + chunk_size]
    
    mock_response.aiter_bytes = mock_aiter_bytes
    
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=None)
    
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.stream = MagicMock(return_value=mock_response)
    
    with patch('hmc_mcp.operations_storage.httpx.AsyncClient', return_value=mock_client):
        temp_file, sha256, size = await _download_iso_from_url(test_url)
        
        assert temp_file.exists()
        assert size == len(test_content)
        assert sha256 == hashlib.sha256(test_content).hexdigest()
        
        # Read back content to verify
        with temp_file.open("rb") as f:
            assert f.read() == test_content
        
        # Clean up
        temp_file.unlink()


@pytest.mark.asyncio
async def test_download_iso_from_https_url_success():
    """Download ISO from HTTPS URL succeeds."""
    test_content = b"Test HTTPS download content\n" * 50
    test_url = "https://example.com/secure.iso"
    
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    
    async def mock_aiter_bytes(chunk_size=8192):
        for i in range(0, len(test_content), chunk_size):
            yield test_content[i:i + chunk_size]
    
    mock_response.aiter_bytes = mock_aiter_bytes
    
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=None)
    
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.stream = MagicMock(return_value=mock_response)
    
    with patch('hmc_mcp.operations_storage.httpx.AsyncClient', return_value=mock_client):
        temp_file, sha256, size = await _download_iso_from_url(test_url)
        
        assert temp_file.exists()
        assert size == len(test_content)
        temp_file.unlink()


@pytest.mark.asyncio
async def test_download_iso_http_error():
    """Download ISO fails on HTTP error."""
    test_url = "http://example.com/notfound.iso"
    
    request = httpx.Request("GET", test_url)
    mock_response = AsyncMock()
    mock_response.status_code = 404
    mock_response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            "404 Not Found",
            request=request,
            response=httpx.Response(404, request=request),
        )
    )
    # Without these the `async with` yields a fresh auto-generated mock, the
    # stubbed `raise_for_status` is never called, and the test passes on an
    # unrelated TypeError instead of the HTTP error it names.
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=None)

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.stream = MagicMock(return_value=mock_response)

    with patch('hmc_mcp.operations_storage.httpx.AsyncClient', return_value=mock_client):
        with pytest.raises(httpx.HTTPStatusError):
            await _download_iso_from_url(test_url)


@pytest.mark.asyncio
async def test_download_iso_size_limit_exceeded():
    """Download ISO fails when size exceeds maximum limit."""
    # Use a small size limit for testing
    test_content = b"X" * 1000  # 1KB content
    small_limit = 500  # Set a very small limit
    
    test_url = "http://example.com/large.iso"
    
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    
    async def mock_aiter_bytes(chunk_size=8192):
        for i in range(0, len(test_content), chunk_size):
            yield test_content[i:i + chunk_size]
    
    mock_response.aiter_bytes = mock_aiter_bytes
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=None)
    
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.stream = MagicMock(return_value=mock_response)
    
    # Patch the size limit to be small
    with patch('hmc_mcp.operations_storage.MAX_DOWNLOAD_SIZE_BYTES', small_limit):
        with patch('hmc_mcp.operations_storage.httpx.AsyncClient', return_value=mock_client):
            with pytest.raises(ValueError, match="exceeds maximum allowed size"):
                await _download_iso_from_url(test_url)




@pytest.mark.asyncio
async def test_download_iso_cleanup_on_error():
    """Download ISO cleans up temp file when download fails."""
    test_url = "http://example.com/error.iso"
    
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    
    async def mock_aiter_bytes(chunk_size=8192):
        yield b"partial content"
        raise Exception("Network error during download")
    
    mock_response.aiter_bytes = mock_aiter_bytes
    
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=None)
    
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.stream = MagicMock(return_value=mock_response)
    
    with patch('hmc_mcp.operations_storage.httpx.AsyncClient', return_value=mock_client):
        with pytest.raises(Exception):
            await _download_iso_from_url(test_url)
        
        # Verify temp file was cleaned up by checking no leftover files
        import glob
        temp_files = glob.glob("/tmp/hmc_upload_*.iso")
        assert len(temp_files) == 0, f"Temp files not cleaned: {temp_files}"


# ---------------------------------------------------------------------------
# G303 — the fetch runs from the MCP server's network position
# ---------------------------------------------------------------------------

# Reachable-looking destinations a caller could name. The metadata address is
# the canonical SSRF target; `localhost:22` stands for a service bound to
# loopback on the server host that the caller cannot route to.
BLOCKED_URLS = (
    "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
    "http://localhost:22/test-image.iso",
    "http://127.0.0.1:8080/test-image.iso",
    "https://internal.corp.test/test-image.iso",
)


@pytest.fixture
def detonate_on_network(monkeypatch):
    """Fail the test if anything resolves a name or opens a socket.

    G303's refusal has to happen *before* the fetch, not after it: a check that
    connects first and refuses afterwards still reaches the destination, and
    still tells the caller whether it answered. Trapping `socket` as well as
    `httpx.AsyncClient` is what makes the timing claim testable — a refusal that
    performs no DNS lookup cannot take longer for a host that resolves than for
    one that does not.
    """
    calls: list[str] = []

    def _trap(name):
        def _boom(*_args, **_kwargs):
            calls.append(name)
            raise AssertionError(f"{name} was reached before the refusal")

        return _boom

    monkeypatch.setattr(socket, "getaddrinfo", _trap("DNS resolution"))
    monkeypatch.setattr(socket, "create_connection", _trap("a connection"))
    # `socket.socket` itself is not trapped: asyncio builds its event loop out of
    # a socketpair, so trapping the constructor fails the test in the loop rather
    # than in the code under test. `connect` is the reaching-out half.
    monkeypatch.setattr(socket.socket, "connect", _trap("a connection"))
    monkeypatch.setattr(
        "hmc_mcp.operations_storage.httpx.AsyncClient", _trap("the HTTP client")
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_storage.resolve_vios_uuid", _trap("the HMC")
    )
    return calls


def _client_for(allowlist: str) -> MagicMock:
    """A stand-in HMC client carrying nothing but the operator's allowlist.

    The allowlist check reads `hmc.config` and must refuse before the client is
    used for anything else, so a mock with no HMC behaviour at all is the point:
    any HMC call would raise instead of returning.
    """
    hmc = MagicMock()
    hmc.config = make_config(iso_url_allowlist=allowlist)
    return hmc


@pytest.mark.parametrize("blocked", BLOCKED_URLS)
@pytest.mark.asyncio
async def test_upload_iso_refuses_a_host_off_the_allowlist_without_connecting(
    blocked, detonate_on_network
):
    """G303: a URL outside the allowlist is refused with no connection opened.

    The tool downloads from the MCP server's network position, so the caller
    picks a destination and the server supplies the reachability — cloud
    instance metadata, loopback services, hosts inside the server's segment.
    """
    with pytest.raises(ValueError) as exc_info:
        await upload_iso(
            _client_for(ISO_HOST), VIOS_UUID, VG_UUID, MEDIA_NAME, blocked
        )

    message = str(exc_info.value)
    assert "allowlist" in message
    assert "HMC_ISO_URL_ALLOWLIST" in message
    assert detonate_on_network == []


@pytest.mark.parametrize("url", (ISO_URL, *BLOCKED_URLS))
@pytest.mark.asyncio
async def test_upload_iso_refuses_every_url_when_no_allowlist_is_configured(
    url, detonate_on_network
):
    """G303: the unset allowlist permits nothing, and says what to set.

    ADR 0050 chose fail-closed over preserving today's behaviour, which breaks
    every existing caller until an operator sets the field. A fail-closed default
    that produced an opaque error would be a support burden, so the message
    naming the setting is part of the decision rather than a nicety.
    """
    with pytest.raises(ValueError) as exc_info:
        await upload_iso(_client_for(""), VIOS_UUID, VG_UUID, MEDIA_NAME, url)

    message = str(exc_info.value)
    assert "HMC_ISO_URL_ALLOWLIST" in message
    assert "iso_url_allowlist" in message
    assert detonate_on_network == []


@pytest.mark.asyncio
async def test_upload_iso_refusal_cannot_distinguish_a_host_that_exists(
    detonate_on_network,
):
    """G303: a blocked host that resolves and one that does not refuse alike.

    A refusal that varied would hand back the oracle the refusal exists to
    remove — a caller could sweep the server's segment for live names. Message
    equality is asserted directly; the timing half is asserted structurally, by
    the `detonate_on_network` trap: no name is resolved on either path, so there
    is no lookup whose success or failure could take a different amount of time.
    """
    resolvable = "http://localhost/test-image.iso"
    unresolvable = "http://no-such-host.invalid/test-image.iso"

    messages = set()
    for candidate in (resolvable, unresolvable):
        with pytest.raises(ValueError) as exc_info:
            await upload_iso(
                _client_for(ISO_HOST), VIOS_UUID, VG_UUID, MEDIA_NAME, candidate
            )
        # Only the caller's own input distinguishes the two.
        messages.add(str(exc_info.value).replace(candidate, "<source>"))

    assert len(messages) == 1
    assert detonate_on_network == []


@pytest.mark.asyncio
async def test_upload_iso_allowlist_entry_with_a_port_permits_only_that_port(
    detonate_on_network,
):
    """G303: `localhost:18765` must not open every loopback service.

    The live-test runner publishes its ISO on one loopback port, so the entry an
    operator writes for it has to bound the port as well as the host — otherwise
    permitting the runner's own server would permit every service bound to
    loopback on the MCP server host.
    """
    with pytest.raises(ValueError) as exc_info:
        await upload_iso(
            _client_for("localhost:18765"),
            VIOS_UUID,
            VG_UUID,
            MEDIA_NAME,
            "http://localhost:22/test-image.iso",
        )

    assert "allowlist" in str(exc_info.value)
    assert detonate_on_network == []


@pytest.mark.asyncio
async def test_upload_iso_matches_the_default_port_of_a_portless_url(
    mock_hmc, stage_download
):
    """G303: `https://host/x` matches an entry pinned to the scheme's port.

    Without this the allowlist would look correct and refuse every real URL, so
    an operator would widen the entry to the whole host to make it work — the
    check talking itself out of its own tightening.
    """
    download = stage_download()
    broker_uri = "https://hmc.test:12443/rest/api/uom/BrokeredFile/broker-port"

    mock_hmc.post(VG_PATH).mock(
        side_effect=[
            httpx.Response(201, text=CREATE_RESPONSE, headers={"Location": broker_uri}),
            httpx.Response(200, text=IMPORT_RESPONSE),
        ]
    )
    mock_hmc.put(broker_uri).mock(return_value=httpx.Response(200, text=""))
    mock_hmc.delete(broker_uri).mock(return_value=httpx.Response(204, text=""))

    config = make_config(iso_url_allowlist=f"{ISO_HOST}:443")
    async with HMCClient(config) as hmc:
        hmc.list_optical_media = AsyncMock(side_effect=[[], []])
        result = await upload_iso(hmc, VIOS_UUID, VG_UUID, MEDIA_NAME, ISO_URL)

    assert result["status"] == "uploaded"
    download.assert_awaited_once_with(ISO_URL)


def _install_iso_transport(monkeypatch, handler):
    """Route `_download_iso_from_url` through a recording mock transport.

    A real `httpx.AsyncClient` on `httpx.MockTransport` rather than a mock
    client: the client-side behaviour under test *is* redirect handling, and a
    hand-rolled mock would answer whatever the test told it to regardless of
    what `follow_redirects` was set to. Every request the client actually emits
    lands in the returned list.

    The patch is installed on the httpx module itself, so it must go in after
    any `HMCClient` has been constructed — the HMC's own client is built in
    `HMCClient.__init__` and has to keep the respx-intercepted transport.
    """
    requests: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    monkeypatch.setattr(
        "hmc_mcp.operations_storage.httpx.AsyncClient",
        functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(_record)),
    )
    return requests


@pytest.mark.asyncio
async def test_upload_iso_uploads_from_an_allowlisted_url_end_to_end(
    mock_hmc, monkeypatch
):
    """G303: the allowlist does not break the path it exists to bound.

    Nothing is stubbed between the URL and the broker: the download runs through
    a real httpx client, the bytes are hashed and staged on disk, and the staged
    file is what reaches the HMC upload.
    """
    broker_uri = "https://hmc.test:12443/rest/api/uom/BrokeredFile/broker-e2e"

    mock_hmc.post(VG_PATH).mock(
        side_effect=[
            httpx.Response(201, text=CREATE_RESPONSE, headers={"Location": broker_uri}),
            httpx.Response(200, text=IMPORT_RESPONSE),
        ]
    )
    uploaded = mock_hmc.put(broker_uri).mock(return_value=httpx.Response(200, text=""))
    mock_hmc.delete(broker_uri).mock(return_value=httpx.Response(204, text=""))

    config = make_config(iso_url_allowlist=ISO_HOST)
    async with HMCClient(config) as hmc:
        requests = _install_iso_transport(
            monkeypatch, lambda _request: httpx.Response(200, content=TEST_CONTENT)
        )
        hmc.list_optical_media = AsyncMock(
            side_effect=[[], [{"MediaName": MEDIA_NAME, "MediaSize": len(TEST_CONTENT)}]]
        )
        result = await upload_iso(hmc, VIOS_UUID, VG_UUID, MEDIA_NAME, ISO_URL)

    assert result["status"] == "uploaded"
    assert result["sha256"] == TEST_SHA256
    assert result["media_size_bytes"] == len(TEST_CONTENT)
    assert [str(request.url) for request in requests] == [ISO_URL]
    assert uploaded.calls.last.request.content == TEST_CONTENT


@pytest.mark.asyncio
async def test_download_iso_refuses_a_redirect_instead_of_following_it(monkeypatch):
    """G303: the URL fetched is the URL the allowlist checked.

    An allowlisted host that answers `302 http://169.254.169.254/…` would
    otherwise carry the fetch to a destination no check ever saw, which is why
    bounding the redirect count was never a fix. `raise_for_status` does not
    cover this on its own — 3xx is not an error status, so an unfollowed
    redirect would be staged and imported as if it were the ISO.
    """
    elsewhere = "http://169.254.169.254/latest/meta-data/"
    requests = _install_iso_transport(
        monkeypatch,
        lambda _request: httpx.Response(302, headers={"Location": elsewhere}),
    )

    with pytest.raises(ValueError) as exc_info:
        await _download_iso_from_url(ISO_URL)

    assert "redirect" in str(exc_info.value)
    # One request, to the URL that was checked: the redirect was not followed.
    assert [str(request.url) for request in requests] == [ISO_URL]


@pytest.mark.asyncio
async def test_download_iso_refuses_a_redirect_without_a_location(monkeypatch):
    """G303: every 3xx is refused, not only the ones httpx calls a redirect.

    `httpx.Response.is_redirect` additionally requires a `Location` header, so a
    bare 3xx would slip past a check written in terms of it and be imported into
    a media repository as an ISO.
    """
    _install_iso_transport(monkeypatch, lambda _request: httpx.Response(304))

    with pytest.raises(ValueError, match="redirect"):
        await _download_iso_from_url(ISO_URL)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("", ()),
        ("images.test", (("images.test", None),)),
        ("localhost:18765", (("localhost", 18765),)),
        (" images.test , localhost:18765 ,", (("images.test", None), ("localhost", 18765))),
        ("IMAGES.TEST", (("images.test", None),)),
        ("[::1]:18765", (("::1", 18765),)),
    ],
)
def test_parse_iso_url_allowlist_accepts_hosts_and_host_ports(value, expected):
    """G303: entries are authorities, and are normalised the way URLs are."""
    assert parse_iso_url_allowlist(value) == expected


@pytest.mark.parametrize(
    "rejected",
    [
        "https://images.test",
        "images.test/isos/",
        "http://images.test/isos",
        "user@images.test",
        "images.test:0",
        "images.test:70000",
        "images.test:iso",
    ],
)
def test_parse_iso_url_allowlist_refuses_anything_that_is_not_an_authority(rejected):
    """G303: a URL-shaped entry fails loudly instead of matching nothing.

    An operator who writes `https://images.test/isos/` and gets a silently empty
    allowlist would conclude the feature is broken and widen it; the whole entry
    is rejected at config load instead, naming what to write.
    """
    with pytest.raises(ValueError) as exc_info:
        parse_iso_url_allowlist(rejected)

    assert rejected in str(exc_info.value)
    assert "HMC_ISO_URL_ALLOWLIST" in str(exc_info.value)


@pytest.mark.parametrize(
    "url", ["http://images.test:0/x.iso", "https://images.test:99999/x.iso"]
)
@pytest.mark.asyncio
async def test_upload_iso_refuses_a_url_whose_port_is_unusable(
    url, detonate_on_network
):
    """G303: an unusable port is refused, not quietly replaced by the default.

    `urlparse` raises on an out-of-range port and yields a falsy 0 for `:0`, so a
    check that fell back to the scheme default on either would compare
    `images.test:99999` against an entry pinned to `images.test:443` and match
    it. The host in these cases is allowlisted; the port is what refuses them.
    """
    with pytest.raises(ValueError, match="usable TCP port"):
        await upload_iso(
            _client_for(f"{ISO_HOST}:443"), VIOS_UUID, VG_UUID, MEDIA_NAME, url
        )

    assert detonate_on_network == []
