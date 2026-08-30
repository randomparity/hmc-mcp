"""Tests for media repository and optical media operations layer."""

from unittest.mock import AsyncMock

import httpx
import pytest

from conftest import make_config

from hmc_mcp.client.core import HMCClient
from hmc_mcp.errors import HMCError
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
VG_UUID = "22222222-2222-2222-2222-222222220001"

VG_ENTRY_WITH_REPO = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:22222222-2222-2222-2222-222222220001</id>
  <title>VolumeGroup:VMLibrary</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <VolumeGroup xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <VolumeGroupUUID>22222222-2222-2222-2222-222222220001</VolumeGroupUUID>
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
  <id>urn:uuid:22222222-2222-2222-2222-222222220002</id>
  <title>VolumeGroup:vg_data</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <VolumeGroup xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <VolumeGroupUUID>22222222-2222-2222-2222-222222220002</VolumeGroupUUID>
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
  <id>urn:uuid:22222222-2222-2222-2222-222222220001</id>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <VolumeGroup xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <VolumeGroupUUID>22222222-2222-2222-2222-222222220001</VolumeGroupUUID>
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
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup/99999999-9999-9999-9999-999999999999"
    ).mock(return_value=httpx.Response(404, text=""))

    async with HMCClient(make_config()) as hmc:
        result = await get_media_repository(
            hmc, None, VIOS_UUID, "99999999-9999-9999-9999-999999999999"
        )

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
_MAPPINGS_GET_PATH = f"{_VIOS_GET_PATH}?group=ViosSCSIMapping"
_VIOS_POST_PATH = (
    f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/VirtualIOServer/{VIOS_UUID}"
)


def _posted_document(post_route) -> str:
    assert post_route.called, "unmount must write the modified VIOS document back"
    return post_route.calls.last.request.content.decode()


def _mock_unmount_reads(mock_hmc, document=VIOS_DOC_WITH_OPTICAL_MAPPINGS):
    mock_hmc.get(_MAPPINGS_GET_PATH).mock(
        return_value=httpx.Response(200, text=document)
    )
    return mock_hmc.get(_VIOS_GET_PATH).mock(
        return_value=httpx.Response(200, text=document)
    )


@pytest.mark.asyncio
async def test_unmount_optical_media_deletes_only_the_exact_mapping_identity():
    """LPAR-scoped inventory resolves MediaName by equality, never subtree text."""
    hmc = AsyncMock()
    hmc.list_optical_mappings.return_value = [
        {
            "UUID": "mapping-prefix",
            "Storage": {
                "VirtualOpticalMedia": {"MediaName": "rhel9.iso.bak"},
            },
        },
        {
            "UUID": "mapping-incidental",
            "Storage": {
                "VirtualOpticalMedia": {"MediaName": "other.iso"},
            },
            "TargetDevice": "rhel9.iso",
        },
        {
            "UUID": "mapping-exact",
            "Storage": {
                "VirtualOpticalMedia": {"MediaName": "rhel9.iso"},
            },
        },
    ]

    await unmount_optical_media(
        hmc, None, VIOS_UUID, LPAR_UUID, media_name="rhel9.iso"
    )

    hmc.list_optical_mappings.assert_awaited_once_with(VIOS_UUID, LPAR_UUID)
    hmc.delete_storage_mapping.assert_awaited_once_with(VIOS_UUID, "mapping-exact")


@pytest.mark.asyncio
async def test_unmount_optical_media_rejects_empty_media_before_inventory():
    hmc = AsyncMock()

    with pytest.raises(HMCError, match="must not be empty"):
        await unmount_optical_media(hmc, None, VIOS_UUID, LPAR_UUID, media_name="")

    hmc.list_optical_mappings.assert_not_awaited()
    hmc.delete_storage_mapping.assert_not_awaited()


@pytest.mark.asyncio
async def test_unmount_optical_media_rejects_ambiguous_exact_identity():
    hmc = AsyncMock()
    hmc.list_optical_mappings.return_value = [
        {
            "UUID": mapping_uuid,
            "Storage": {
                "VirtualOpticalMedia": {"MediaName": "rhel9.iso"},
            },
        }
        for mapping_uuid in ("mapping-a", "mapping-b")
    ]

    with pytest.raises(HMCError, match="ambiguous"):
        await unmount_optical_media(
            hmc, None, VIOS_UUID, LPAR_UUID, media_name="rhel9.iso"
        )

    hmc.delete_storage_mapping.assert_not_awaited()


@pytest.mark.asyncio
async def test_unmount_optical_media_fails_closed_when_exact_mapping_is_absent():
    hmc = AsyncMock()
    hmc.list_optical_mappings.return_value = [
        {
            "UUID": "mapping-prefix",
            "Storage": {
                "VirtualOpticalMedia": {"MediaName": "rhel9.iso.bak"},
            },
            "TargetDevice": "rhel9.iso",
        }
    ]

    with pytest.raises(HMCError, match="not found"):
        await unmount_optical_media(
            hmc, None, VIOS_UUID, LPAR_UUID, media_name="rhel9.iso"
        )

    hmc.delete_storage_mapping.assert_not_awaited()


@pytest.mark.asyncio
async def test_unmount_optical_media_rejects_missing_mapping_uuid():
    hmc = AsyncMock()
    hmc.list_optical_mappings.return_value = [
        {
            "Storage": {
                "VirtualOpticalMedia": {"MediaName": "rhel9.iso"},
            },
        }
    ]

    with pytest.raises(HMCError, match="invalid UUID identity"):
        await unmount_optical_media(
            hmc, None, VIOS_UUID, LPAR_UUID, media_name="rhel9.iso"
        )

    hmc.delete_storage_mapping.assert_not_awaited()


@pytest.mark.asyncio
async def test_unmount_optical_media_removes_the_named_mapping_for_that_lpar(mock_hmc):
    """Unmount drops the addressed mapping and rewrites nothing else.

    The HMC has no UUID-addressable VirtualSCSIMapping sub-resource, so the
    operation is a read-modify-write of the whole VirtualIOServer document.
    This asserts what that document says afterwards, not that a call happened.

    The fixture also carries the same media name on another LPAR, proving the
    LPAR-scoped inventory identity is part of the selection.
    """
    _mock_unmount_reads(mock_hmc)
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
    _mock_unmount_reads(mock_hmc)
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
async def test_unmount_optical_media_fails_without_post_when_mapping_is_absent(mock_hmc):
    """An unmatched exact identity fails closed without rewriting the VIOS."""
    mock_hmc.get(_MAPPINGS_GET_PATH).mock(
        return_value=httpx.Response(200, text=VIOS_DOC_WITH_OPTICAL_MAPPINGS)
    )
    post = mock_hmc.post(_VIOS_POST_PATH).mock(return_value=httpx.Response(200, text=""))

    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError, match="not found"):
            await unmount_optical_media(
                hmc, None, VIOS_UUID, LPAR_UUID, media_name="aix73.iso"
            )

    assert not post.called


@pytest.mark.asyncio
async def test_unmount_optical_media_preserves_a_sibling_with_a_prefix_name(
    mock_hmc,
):
    """A strict prefix sibling survives while the exact mapping is removed."""
    doc = VIOS_DOC_WITH_OPTICAL_MAPPINGS.replace(
        "<UUID>mapping-disk-001</UUID>", "<UUID>mapping-optical-backup</UUID>"
    ).replace(
        "<Storage><VirtualDisk><DiskName>lv_boot</DiskName></VirtualDisk></Storage>",
        "<Storage><VirtualOpticalMedia><MediaName>rhel9.iso.bak</MediaName>"
        "</VirtualOpticalMedia></Storage>",
    )
    _mock_unmount_reads(mock_hmc, doc)
    post = mock_hmc.post(_VIOS_POST_PATH).mock(return_value=httpx.Response(200, text=""))

    async with HMCClient(make_config()) as hmc:
        await unmount_optical_media(
            hmc, None, VIOS_UUID, LPAR_UUID, media_name="rhel9.iso"
        )

    body = _posted_document(post)
    assert "mapping-optical-backup" in body
    assert "mapping-optical-target" not in body


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
    _mock_unmount_reads(mock_hmc)
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


def test_optical_unmount_reuses_the_storage_mapping_remover():
    """The client has one parent-VIOS VirtualSCSIMapping remover."""
    assert not hasattr(HMCClient, "delete_optical_mapping")
