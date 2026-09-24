"""Mapping creates read-modify-write the VIOS ViosSCSIMapping group (issue #962, ADR 0169).

The existing mapping is the observed V10R3 fixture; a create must post it back
unchanged beside the new mapping, under the GET's ETag.
"""

import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
import pytest
from conftest import make_config

from hmcpctl.client.core import HMCClient
from hmcpctl.errors import HMCError

UOM_NS = "http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/"
VIOS_UUID = "00000000-0000-4000-8000-000000000003"
LPAR_UUID = "00000000-0000-4000-8000-0000000000BB"
PATH = f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}?group=ViosSCSIMapping"
OBSERVED = (
    Path(__file__).with_name("vscsi_mapping_v10r3.xml").read_text(encoding="utf-8")
)
OBSERVED_MAPPINGS = OBSERVED[OBSERVED.index("<VirtualSCSIMappings") :]


def vios_entry(mappings: str = OBSERVED_MAPPINGS, uuid: str = VIOS_UUID) -> str:
    return f"""<entry xmlns="http://www.w3.org/2005/Atom"><content>
      <VirtualIOServer xmlns="{UOM_NS}" schemaVersion="V1_0">
        <Metadata><Atom><AtomID>{uuid}</AtomID></Atom></Metadata>
        <PartitionUUID kb="ROO">{uuid}</PartitionUUID>
        {mappings}
      </VirtualIOServer></content></entry>"""


def _mappings(xml: str) -> list[ET.Element]:
    return ET.fromstring(xml).findall(f".//{{{UOM_NS}}}VirtualSCSIMapping")


def _canonical(element: ET.Element) -> str:
    return ET.canonicalize(ET.tostring(element, encoding="unicode"), strip_text=True)


def _children(element: ET.Element) -> list[str]:
    return [child.tag.split("}")[1] for child in element]


def _routes(mock_hmc, get_response, post_status=200):
    get = mock_hmc.get(PATH).mock(return_value=get_response)
    post = mock_hmc.post(PATH).mock(return_value=httpx.Response(post_status, text=""))
    return get, post


def _ok(body: str = vios_entry()) -> httpx.Response:
    return httpx.Response(200, text=body, headers={"ETag": '"etag-1"'})


async def _map(hmc: HMCClient) -> None:
    await hmc.map_storage_to_lpar(VIOS_UUID, "VirtualDisk", "vd-R2", LPAR_UUID, "vtscsi9")


async def _mount(hmc: HMCClient) -> None:
    await hmc.create_optical_mapping(VIOS_UUID, "install.iso", LPAR_UUID, "vtopt9")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("create", "storage"), [(_map, "VirtualDisk"), (_mount, "VirtualOpticalMedia")]
)
async def test_create_posts_existing_mappings_unchanged_under_if_match(
    mock_hmc, create, storage
):
    get, post = _routes(mock_hmc, _ok())

    async with HMCClient(make_config()) as hmc:
        await create(hmc)

    assert get.call_count == 1 and post.call_count == 1
    request = post.calls.last.request
    assert request.headers["If-Match"] == '"etag-1"'
    assert request.headers["Accept"] == "*/*"
    assert request.headers["Content-Type"].endswith("; type=VirtualIOServer")
    existing, new = _mappings(request.content.decode())
    assert _canonical(existing) == _canonical(_mappings(vios_entry())[0])
    assert _children(new) == [
        "Metadata",
        "AssociatedLogicalPartition",
        "Storage",
        "TargetDevice",
    ]
    assert new.find(f"{{{UOM_NS}}}Storage/{{{UOM_NS}}}{storage}") is not None
    link = new.find(f"{{{UOM_NS}}}AssociatedLogicalPartition").get("href")
    assert link.endswith(f"/LogicalPartition/{LPAR_UUID}")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(200, text=vios_entry()), "no ETag"),
        (
            httpx.Response(200, text=vios_entry(""), headers={"ETag": "e"}),
            "no VirtualSCSIMappings",
        ),
        (
            httpx.Response(
                200, text=vios_entry(uuid=LPAR_UUID), headers={"ETag": "e"}
            ),
            "does not match",
        ),
    ],
)
async def test_create_refuses_to_post_without_etag_collection_or_identity(
    mock_hmc, response, message
):
    _, post = _routes(mock_hmc, response)

    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError, match=message):
            await _map(hmc)

    assert not post.called


@pytest.mark.asyncio
async def test_create_rejects_a_response_with_xml_entities(mock_hmc):
    """An entity-bearing GET body raises HMCError, not a raw DefusedXmlException."""
    document = '<!DOCTYPE x [<!ENTITY payload "expanded">]><x>&payload;</x>'
    _, post = _routes(
        mock_hmc, httpx.Response(200, text=document, headers={"ETag": '"etag-1"'})
    )

    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError, match="not valid XML"):
            await _map(hmc)

    assert not post.called


@pytest.mark.asyncio
async def test_create_reports_a_concurrent_change_on_412(mock_hmc):
    _, post = _routes(mock_hmc, _ok(), post_status=412)

    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError, match="changed since they were read") as raised:
            await _mount(hmc)

    assert raised.value.status_code == 412
    assert post.call_count == 1
