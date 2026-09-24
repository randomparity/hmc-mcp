"""Media-repository VolumeGroup writes are conditioned on the GET's ETag (#996, ADR 0171).

Each of the four media writes GETs the whole VolumeGroup and POSTs it back. Like the
virtual-disk writes, the POST carries If-Match set to the GET's ETag, a GET without one
refuses before any POST, and a 412 is the concurrent-change error with nothing written.
"""

import httpx
import pytest
from conftest import make_config

from hmcpctl.client.core import HMCClient
from hmcpctl.errors import HMCError

VIOS = "11111111-1111-1111-1111-111111111111"
VG = "22222222-2222-2222-2222-222222222222"
VG_PATH = f"/rest/api/uom/VirtualIOServer/{VIOS}/VolumeGroup/{VG}"
ETAG = '"etag-1"'

_REPOSITORY = """
        <MediaRepositories>
          <VirtualMediaRepository>
            <OpticalMedia>
              <VirtualOpticalMedia>
                <MediaName>old.iso</MediaName>
              </VirtualOpticalMedia>
            </OpticalMedia>
            <RepositoryName>VMLibrary</RepositoryName>
            <RepositorySize>7</RepositorySize>
          </VirtualMediaRepository>
        </MediaRepositories>"""


def _feed(repository: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:{VG}</id>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <VolumeGroup xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <GroupName>clientvg1</GroupName>{repository}
      </VolumeGroup>
    </content>
  </entry>
</feed>"""


# (operation, arguments, repository present in the fetched group)
OPERATIONS = [
    ("create_media_repository", (2048,), False),
    ("create_optical_media", ("new.iso", 3072), True),
    ("delete_media_repository", (), True),
    ("delete_optical_media", ("old.iso",), True),
]
IDS = [name for name, _, _ in OPERATIONS]


def _routes(mock_hmc, has_repository: bool, *, etag: str | None = ETAG, post_status=200):
    feed = _feed(_REPOSITORY if has_repository else "")
    headers = {"ETag": etag} if etag else {}
    mock_hmc.get(VG_PATH).mock(return_value=httpx.Response(200, text=feed, headers=headers))
    return mock_hmc.post(VG_PATH).mock(return_value=httpx.Response(post_status, text=feed))


async def _run(operation: str, arguments: tuple):
    async with HMCClient(make_config()) as hmc:
        return await getattr(hmc, operation)(VIOS, VG, *arguments)


@pytest.mark.asyncio
@pytest.mark.parametrize(("operation", "arguments", "has_repository"), OPERATIONS, ids=IDS)
async def test_media_write_sends_if_match(mock_hmc, operation, arguments, has_repository):
    route = _routes(mock_hmc, has_repository)

    await _run(operation, arguments)

    assert route.call_count == 1
    assert route.calls.last.request.headers["If-Match"] == ETAG


@pytest.mark.asyncio
@pytest.mark.parametrize(("operation", "arguments", "has_repository"), OPERATIONS, ids=IDS)
async def test_media_write_refuses_without_etag(
    mock_hmc, operation, arguments, has_repository
):
    route = _routes(mock_hmc, has_repository, etag=None)

    with pytest.raises(HMCError, match="no ETag"):
        await _run(operation, arguments)

    assert not route.called


@pytest.mark.asyncio
@pytest.mark.parametrize(("operation", "arguments", "has_repository"), OPERATIONS, ids=IDS)
async def test_media_write_reports_stale_etag(mock_hmc, operation, arguments, has_repository):
    route = _routes(mock_hmc, has_repository, post_status=412)

    with pytest.raises(HMCError, match="changed since it was read") as exc_info:
        await _run(operation, arguments)

    assert exc_info.value.status_code == 412
    assert route.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "arguments", "has_repository"),
    [
        ("create_media_repository", (7168,), True),
        ("delete_media_repository", (), False),
        ("delete_optical_media", ("absent.iso",), True),
    ],
    ids=["repository-exists", "no-repository", "no-such-medium"],
)
async def test_media_noop_needs_no_etag(mock_hmc, operation, arguments, has_repository):
    """A write that has nothing to change posts nothing, so it needs no ETag."""
    route = _routes(mock_hmc, has_repository, etag=None)

    await _run(operation, arguments)

    assert not route.called
