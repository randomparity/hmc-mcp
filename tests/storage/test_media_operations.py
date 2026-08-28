"""Tests for media repository and optical media operations layer."""

import httpx
import pytest

from conftest import make_config

from hmc_mcp.client.core import HMCClient
from hmc_mcp.operations.storage import (
    get_media_repository,
    list_optical_media,
    unmount_optical_media,
)


@pytest.fixture(autouse=True)
def _authorize_lpar_mutations(monkeypatch):
    async def authorize(hmc, system, lpar, **_kwargs):
        from hmc_mcp.resource_identity import resolve_lpar_uuid

        return await resolve_lpar_uuid(hmc, lpar, system_name_or_uuid=system)

    monkeypatch.setattr(
        "hmc_mcp.operations.storage.resolve_and_authorize_lpar_mutation", authorize
    )

VIOS_UUID = "00000000-0000-0000-0000-000000000003"
VG_UUID = "vg-uuid-0001"

VG_ENTRY_WITH_REPO = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:vg-uuid-0001</id>
  <title>VolumeGroup:VMLibrary</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <VolumeGroup xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <VolumeGroupUUID>vg-uuid-0001</VolumeGroupUUID>
      <GroupName>VMLibrary</GroupName>
      <VirtualMediaRepository schemaVersion="V1_0">
        <RepositoryName>VMLibrary</RepositoryName>
        <RepositorySize>40960</RepositorySize>
      </VirtualMediaRepository>
    </VolumeGroup>
  </content>
</entry>
"""

VG_ENTRY_EMPTY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:vg-uuid-0002</id>
  <title>VolumeGroup:vg_data</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <VolumeGroup xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <VolumeGroupUUID>vg-uuid-0002</VolumeGroupUUID>
      <GroupName>vg_data</GroupName>
    </VolumeGroup>
  </content>
</entry>
"""


@pytest.mark.asyncio
async def test_get_media_repository_operation(mock_hmc):
    """get_media_repository calls client method and returns repository info."""
    route = mock_hmc.get(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup/{VG_UUID}"
    ).mock(return_value=httpx.Response(200, text=VG_ENTRY_WITH_REPO))

    async with HMCClient(make_config()) as hmc:
        result = await get_media_repository(hmc, None, VIOS_UUID, VG_UUID)

    assert route.called
    assert result is not None
    resource = result["Resource"]
    repo = resource["VirtualMediaRepository"]
    assert repo["RepositoryName"] == "VMLibrary"
    assert repo["RepositorySize"] == "40960"


@pytest.mark.asyncio
async def test_list_optical_media_operation(mock_hmc):
    """list_optical_media calls client method and returns optical media list."""
    vg_entry_with_media = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:vg-uuid-0001</id>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <VolumeGroup xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <VolumeGroupUUID>vg-uuid-0001</VolumeGroupUUID>
      <VirtualMediaRepository schemaVersion="V1_0">
        <RepositoryName>VMLibrary</RepositoryName>
        <RepositorySize>40960</RepositorySize>
        <VirtualOpticalMedia schemaVersion="V1_0">
          <MediaName>aix.iso</MediaName>
          <MediaSize>1400</MediaSize>
          <MediaType>BLANK</MediaType>
        </VirtualOpticalMedia>
      </VirtualMediaRepository>
    </VolumeGroup>
  </content>
</entry>
"""
    route = mock_hmc.get(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup/{VG_UUID}"
    ).mock(return_value=httpx.Response(200, text=vg_entry_with_media))

    async with HMCClient(make_config()) as hmc:
        media_list = await list_optical_media(hmc, None, VIOS_UUID, VG_UUID)

    assert route.called
    assert len(media_list) == 1
    assert media_list[0]["MediaName"] == "aix.iso"


@pytest.mark.asyncio
async def test_get_media_repository_none_propagates(mock_hmc):
    """get_media_repository propagates None when repository not found."""
    route = mock_hmc.get(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup/missing-vg"
    ).mock(return_value=httpx.Response(404, text=""))

    async with HMCClient(make_config()) as hmc:
        result = await get_media_repository(hmc, None, VIOS_UUID, "missing-vg")

    assert route.called
    assert result is None


@pytest.mark.asyncio
async def test_list_optical_media_empty_propagates(mock_hmc):
    """list_optical_media propagates empty list when no media or VG not found."""
    route = mock_hmc.get(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup/{VG_UUID}"
    ).mock(return_value=httpx.Response(200, text=VG_ENTRY_EMPTY))

    async with HMCClient(make_config()) as hmc:
        media_list = await list_optical_media(hmc, None, VIOS_UUID, VG_UUID)

    assert route.called
    assert media_list == []

SYSTEM_UUID = "00000000-0000-0000-0000-000000000004"
LPAR_UUID = "11111111-1111-1111-1111-111111111111"
OTHER_LPAR_UUID = "22222222-2222-2222-2222-222222222222"

VIOS_DOC_WITH_OPTICAL_MAPPINGS = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:{VIOS_UUID}</id>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <VirtualIOServer xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <UUID>{VIOS_UUID}</UUID>
      <PartitionName>vios1</PartitionName>
      <AssociatedManagedSystem rel="related"
        href="https://hmc/rest/api/uom/ManagedSystem/{SYSTEM_UUID}"/>
      <VirtualSCSIMappings>
        <VirtualSCSIMapping>
          <UUID>mapping-disk-001</UUID>
          <Storage><VirtualDisk><DiskName>lv_boot</DiskName></VirtualDisk></Storage>
          <AssociatedLogicalPartition rel="related"
            href="/rest/api/uom/LogicalPartition/{LPAR_UUID}"/>
        </VirtualSCSIMapping>
        <VirtualSCSIMapping>
          <UUID>mapping-optical-target</UUID>
          <Storage>
            <VirtualOpticalMedia><MediaName>rhel9.iso</MediaName></VirtualOpticalMedia>
          </Storage>
          <TargetDevice>vtopt0</TargetDevice>
          <AssociatedLogicalPartition rel="related"
            href="/rest/api/uom/LogicalPartition/{LPAR_UUID}"/>
        </VirtualSCSIMapping>
        <VirtualSCSIMapping>
          <UUID>mapping-optical-other-lpar</UUID>
          <Storage>
            <VirtualOpticalMedia><MediaName>rhel9.iso</MediaName></VirtualOpticalMedia>
          </Storage>
          <TargetDevice>vtopt1</TargetDevice>
          <AssociatedLogicalPartition rel="related"
            href="/rest/api/uom/LogicalPartition/{OTHER_LPAR_UUID}"/>
        </VirtualSCSIMapping>
      </VirtualSCSIMappings>
    </VirtualIOServer>
  </content>
</entry>
"""

_VIOS_GET_PATH = f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}"
_VIOS_POST_PATH = (
    f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/VirtualIOServer/{VIOS_UUID}"
)


def _posted_document(post_route) -> str:
    assert post_route.called, "unmount must write the modified VIOS document back"
    return post_route.calls.last.request.content.decode()


@pytest.mark.asyncio
async def test_unmount_optical_media_removes_the_named_mapping_for_that_lpar(mock_hmc):
    """Unmount drops the addressed mapping and rewrites nothing else.

    The HMC has no UUID-addressable VirtualSCSIMapping sub-resource, so the
    operation is a read-modify-write of the whole VirtualIOServer document.
    This asserts what that document says afterwards, not that a call happened.

    Containment holds here because the fixture's names do not overlap; it is
    not a property the selector enforces. See #439 and the prefix-collision
    characterization test below.
    """
    mock_hmc.get(_VIOS_GET_PATH).mock(
        return_value=httpx.Response(200, text=VIOS_DOC_WITH_OPTICAL_MAPPINGS)
    )
    post = mock_hmc.post(_VIOS_POST_PATH).mock(return_value=httpx.Response(200, text=""))

    async with HMCClient(make_config()) as hmc:
        result = await unmount_optical_media(
            hmc, None, VIOS_UUID, LPAR_UUID, media_name="rhel9.iso"
        )

    assert result is None
    body = _posted_document(post)
    assert "mapping-optical-target" not in body
    assert "vtopt0" not in body
    assert "mapping-disk-001" in body
    assert "mapping-optical-other-lpar" in body


@pytest.mark.asyncio
async def test_unmount_optical_media_preserves_the_backing_iso(mock_hmc):
    """Unmount removes the mapping only; no VirtualOpticalMedia is deleted.

    The surviving mapping keeps its VirtualOpticalMedia element, and the
    operation issues no DELETE against the media repository, so the ISO
    container stays available for a later remount.
    """
    mock_hmc.get(_VIOS_GET_PATH).mock(
        return_value=httpx.Response(200, text=VIOS_DOC_WITH_OPTICAL_MAPPINGS)
    )
    post = mock_hmc.post(_VIOS_POST_PATH).mock(return_value=httpx.Response(200, text=""))

    async with HMCClient(make_config()) as hmc:
        await unmount_optical_media(
            hmc, None, VIOS_UUID, LPAR_UUID, media_name="rhel9.iso"
        )

    body = _posted_document(post)
    assert "VirtualOpticalMedia" in body
    assert "rhel9.iso" in body
    deletes = [
        call.request
        for call in mock_hmc.calls
        if call.request.method == "DELETE"
        and "/rest/api/web/Logon" not in call.request.url.path
    ]
    assert deletes == []


@pytest.mark.asyncio
async def test_unmount_optical_media_currently_no_ops_when_no_mapping_matches(mock_hmc):
    """Characterization: an unmatched media name writes nothing and succeeds.

    This pins today's behavior, it does not endorse it. ADR 0079 decides that a
    destructive VirtualSCSIMapping removal fails closed when the mapping cannot
    be proven to exist, and lists "silently succeed when no mapping matches"
    among its rejected alternatives. The optical path predates that decision and
    still diverges; #439 owns the reconciliation and flips this assertion.
    """
    mock_hmc.get(_VIOS_GET_PATH).mock(
        return_value=httpx.Response(200, text=VIOS_DOC_WITH_OPTICAL_MAPPINGS)
    )
    post = mock_hmc.post(_VIOS_POST_PATH).mock(return_value=httpx.Response(200, text=""))

    async with HMCClient(make_config()) as hmc:
        await unmount_optical_media(
            hmc, None, VIOS_UUID, LPAR_UUID, media_name="aix73.iso"
        )

    assert not post.called


@pytest.mark.asyncio
async def test_unmount_optical_media_substring_selector_matches_a_sibling_prefix(
    mock_hmc,
):
    """Characterization: the selector is a substring test, so prefixes collide.

    ``delete_optical_mapping`` chooses its victim with ``media_name in
    ET.tostring(mapping)``, so unmounting ``rhel9.iso`` can remove a mapping
    backed by ``rhel9.iso.bak`` when that mapping comes first in document order.
    This is a defect, not a contract: #439 replaces the substring test with
    element-wise MediaName equality, at which point this test asserts the
    opposite.
    """
    doc = VIOS_DOC_WITH_OPTICAL_MAPPINGS.replace(
        "<UUID>mapping-disk-001</UUID>", "<UUID>mapping-optical-backup</UUID>"
    ).replace(
        "<Storage><VirtualDisk><DiskName>lv_boot</DiskName></VirtualDisk></Storage>",
        "<Storage><VirtualOpticalMedia><MediaName>rhel9.iso.bak</MediaName>"
        "</VirtualOpticalMedia></Storage>",
    )
    mock_hmc.get(_VIOS_GET_PATH).mock(return_value=httpx.Response(200, text=doc))
    post = mock_hmc.post(_VIOS_POST_PATH).mock(return_value=httpx.Response(200, text=""))

    async with HMCClient(make_config()) as hmc:
        await unmount_optical_media(
            hmc, None, VIOS_UUID, LPAR_UUID, media_name="rhel9.iso"
        )

    body = _posted_document(post)
    assert "mapping-optical-backup" not in body, (
        "known defect (#439): the substring selector removed the .bak sibling"
    )
    assert "mapping-optical-target" in body


@pytest.mark.asyncio
async def test_unmount_optical_media_resolves_vios_and_lpar_names(mock_hmc):
    """Names are resolved to UUIDs before the mapping is addressed."""
    vios_search = f"""<feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <id>urn:uuid:{VIOS_UUID}</id>
      <content type="application/vnd.ibm.powervm.uom+xml">
        <VirtualIOServer xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
          <PartitionName>vios1</PartitionName>
        </VirtualIOServer>
      </content>
    </entry></feed>"""
    lpar_search = f"""<feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <id>urn:uuid:{LPAR_UUID}</id>
      <content type="application/vnd.ibm.powervm.uom+xml">
        <LogicalPartition xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
          <PartitionName>lpar1</PartitionName>
        </LogicalPartition>
      </content>
    </entry></feed>"""
    mock_hmc.get("/rest/api/uom/VirtualIOServer/search/(PartitionName==vios1)").mock(
        return_value=httpx.Response(200, text=vios_search)
    )
    mock_hmc.get("/rest/api/uom/LogicalPartition/search/(PartitionName==lpar1)").mock(
        return_value=httpx.Response(200, text=lpar_search)
    )
    mock_hmc.get(_VIOS_GET_PATH).mock(
        return_value=httpx.Response(200, text=VIOS_DOC_WITH_OPTICAL_MAPPINGS)
    )
    post = mock_hmc.post(_VIOS_POST_PATH).mock(return_value=httpx.Response(200, text=""))

    async with HMCClient(make_config()) as hmc:
        await unmount_optical_media(
            hmc, None, "vios1", "lpar1", media_name="rhel9.iso"
        )

    assert "mapping-optical-target" not in _posted_document(post)


def test_detach_optical_mapping_alias_is_gone():
    """Issue #362: the duplicate name is removed outright, with no shim."""
    import hmc_mcp.operations.storage as ops

    assert not hasattr(ops, "detach_optical_mapping")


def test_unmount_docstrings_carry_both_halves_of_the_selector_caveat():
    """The #439 mitigation is prose; pin it so a tidy-up cannot silently drop it.

    ``hmc_unmount_optical_media`` is destructive and its selector can both remove
    the wrong mapping and silently remove nothing. Until #439 makes the client
    fail closed, these docstrings are the only thing standing between an agent
    and a wrongly detached boot disk, and nothing else in the suite asserts they
    still say so. Same contract as tests/app/test_user_tool_contracts.py.
    """
    from hmc_mcp.server_tools import storage as server_storage
    from hmc_mcp.operations import storage as operations_storage

    for handler in (
        server_storage.hmc_unmount_optical_media,
        operations_storage.unmount_optical_media,
    ):
        assert handler.__doc__ is not None
        doc = " ".join(handler.__doc__.split())
        assert "substring" in doc, f"{handler.__name__}: over-match half missing"
        assert "boot disk" in doc, f"{handler.__name__}: over-match blast radius missing"
        assert "no mapping" in doc, f"{handler.__name__}: silent-miss half missing"
        assert "#439" in doc, f"{handler.__name__}: owning issue missing"
        assert "list_storage_mappings" in doc, (
            f"{handler.__name__}: must name the unfiltered inventory, since "
            "list_optical_mappings cannot see a wrongly removed disk mapping"
        )
