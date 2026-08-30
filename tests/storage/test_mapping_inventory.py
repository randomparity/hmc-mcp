"""Transport-layer tests for VirtualSCSI mapping inventory.

Regression coverage for issue #348: list_storage_mappings must read the
mapping block from the parsed Resource (not the Atom entry) and the
lpar_uuid filters must match the href key element_to_dict actually
produces, so both previously-dead paths fire.
"""

import os
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET

import httpx
import pytest
from defusedxml.common import EntitiesForbidden

from conftest import make_config

from hmc_mcp.client.core import HMCClient
from hmc_mcp.errors import HMCError

VIOS_UUID = "00000000-0000-0000-0000-000000000003"
SYSTEM_UUID = "00000000-0000-0000-0000-000000000004"

MAPPINGS_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:uom="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
  <entry>
    <content>
      <VirtualIOServer>
        <VirtualSCSIMappings>
          <VirtualSCSIMapping>
            <Metadata><Atom/></Metadata>
            <UUID>mapping-uuid-disk-001</UUID>
            <Storage>
              <VirtualDisk>
                <DiskName>lv_boot</DiskName>
              </VirtualDisk>
            </Storage>
            <TargetDevice>vhost0</TargetDevice>
            <AssociatedLogicalPartition rel="related" href="/rest/api/uom/LogicalPartition/33333333-3333-3333-3333-333333330001"/>
          </VirtualSCSIMapping>
          <VirtualSCSIMapping>
            <Metadata><Atom/></Metadata>
            <UUID>mapping-uuid-pv-001</UUID>
            <Storage>
              <PhysicalVolume>
                <VolumeName>hdisk5</VolumeName>
              </PhysicalVolume>
            </Storage>
            <AssociatedLogicalPartition rel="related" href="/rest/api/uom/LogicalPartition/33333333-3333-3333-3333-333333330002"/>
          </VirtualSCSIMapping>
        </VirtualSCSIMappings>
      </VirtualIOServer>
    </content>
  </entry>
</feed>
"""

EMPTY_MAPPINGS_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <content>
      <VirtualIOServer>
        <VirtualSCSIMappings/>
      </VirtualIOServer>
    </content>
  </entry>
</feed>
"""

# Reuses the optical inventory feed shape: two optical mappings split
# across two LPARs plus one disk mapping, to prove the lpar_uuid filter
# on list_optical_mappings bites.
OPTICAL_MAPPINGS_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:uom="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
  <entry>
    <content>
      <VirtualIOServer>
        <VirtualSCSIMappings>
          <VirtualSCSIMapping>
            <Metadata><Atom/></Metadata>
            <UUID>mapping-uuid-optical-001</UUID>
            <Storage>
              <VirtualOpticalMedia>
                <MediaName>aix72.iso</MediaName>
              </VirtualOpticalMedia>
            </Storage>
            <AssociatedLogicalPartition rel="related" href="/rest/api/uom/LogicalPartition/33333333-3333-3333-3333-333333330001"/>
          </VirtualSCSIMapping>
          <VirtualSCSIMapping>
            <Metadata><Atom/></Metadata>
            <UUID>mapping-uuid-optical-002</UUID>
            <Storage>
              <VirtualOpticalMedia>
                <MediaName>rhel8.iso</MediaName>
              </VirtualOpticalMedia>
            </Storage>
            <AssociatedLogicalPartition rel="related" href="/rest/api/uom/LogicalPartition/33333333-3333-3333-3333-333333330002"/>
          </VirtualSCSIMapping>
        </VirtualSCSIMappings>
      </VirtualIOServer>
    </content>
  </entry>
</feed>
"""

VIOS_PATH = f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}?group=ViosSCSIMapping"
VIOS_PARENT_PATH = f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}"
VIOS_POST_PATH = f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/VirtualIOServer/{VIOS_UUID}"
VIOS_PARENT = f"""<VirtualIOServer
  xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
  <UUID>{VIOS_UUID}</UUID>
  <UnrelatedLink href="/rest/api/uom/ManagedSystem/11111111-1111-1111-1111-111111111111"/>
  <AssociatedManagedSystem href="/rest/api/uom/ManagedSystem/{SYSTEM_UUID}"/>
  <VirtualSCSIMappings>
    <VirtualSCSIMapping><UUID>mapping-1</UUID></VirtualSCSIMapping>
    <VirtualSCSIMapping><UUID>mapping-10</UUID></VirtualSCSIMapping>
  </VirtualSCSIMappings>
  <ResourceMonitoringControlState>active</ResourceMonitoringControlState>
</VirtualIOServer>"""


@pytest.mark.asyncio
async def test_list_storage_mappings_reads_resource(mock_hmc):
    """Mappings live under entries[0]["Resource"], not on the entry itself."""
    mock_hmc.get(VIOS_PATH).mock(
        return_value=httpx.Response(200, text=MAPPINGS_FEED)
    )

    config = make_config()
    async with HMCClient(config) as hmc:
        mappings = await hmc.list_storage_mappings(VIOS_UUID)

    assert len(mappings) == 2
    names = sorted(
        next(iter(m["Storage"].values()))["VolumeName"]
        if "PhysicalVolume" in m["Storage"]
        else next(iter(m["Storage"].values()))["DiskName"]
        for m in mappings
    )
    assert names == ["hdisk5", "lv_boot"]


@pytest.mark.asyncio
async def test_list_storage_mappings_empty(mock_hmc):
    """list_storage_mappings returns empty list when no mappings exist."""
    mock_hmc.get(VIOS_PATH).mock(
        return_value=httpx.Response(200, text=EMPTY_MAPPINGS_FEED)
    )

    config = make_config()
    async with HMCClient(config) as hmc:
        mappings = await hmc.list_storage_mappings(VIOS_UUID)

    assert mappings == []


@pytest.mark.asyncio
async def test_list_storage_mappings_propagates_bad_request(mock_hmc):
    """A rejected documented group is an API error, not an empty inventory."""
    mock_hmc.get(VIOS_PATH).mock(return_value=httpx.Response(400, text="bad request"))

    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError) as raised:
            await hmc.list_storage_mappings(VIOS_UUID)

    assert raised.value.status_code == 400


@pytest.mark.asyncio
async def test_list_storage_mappings_filters_by_lpar(mock_hmc):
    """The lpar_uuid filter matches the parsed href key and keeps only that LPAR's mappings."""
    mock_hmc.get(VIOS_PATH).mock(
        return_value=httpx.Response(200, text=MAPPINGS_FEED)
    )

    config = make_config()
    async with HMCClient(config) as hmc:
        mappings = await hmc.list_storage_mappings(VIOS_UUID, "33333333-3333-3333-3333-333333330002")

    assert len(mappings) == 1
    assert mappings[0]["UUID"] == "mapping-uuid-pv-001"
    assert (
        mappings[0]["AssociatedLogicalPartition"]["href"]
        == "/rest/api/uom/LogicalPartition/33333333-3333-3333-3333-333333330002"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lpar_href",
    [
        "/rest/api/uom/LogicalPartition/33333333-3333-3333-3333-333333330001",
        "https://hmc.test:12443/rest/api/uom/LogicalPartition/33333333-3333-3333-3333-333333330001",
    ],
    ids=["relative", "absolute"],
)
async def test_list_optical_mappings_filters_by_exact_lpar_path(
    mock_hmc, lpar_href
):
    """Relative and absolute hrefs identify the same exact LPAR path."""
    feed = OPTICAL_MAPPINGS_FEED.replace(
        "/rest/api/uom/LogicalPartition/33333333-3333-3333-3333-333333330001", lpar_href
    )
    mock_hmc.get(VIOS_PATH).mock(
        return_value=httpx.Response(200, text=feed)
    )

    config = make_config()
    async with HMCClient(config) as hmc:
        mappings = await hmc.list_optical_mappings(VIOS_UUID, "33333333-3333-3333-3333-333333330001")

    assert len(mappings) == 1
    assert mappings[0]["Storage"]["VirtualOpticalMedia"]["MediaName"] == "aix72.iso"


@pytest.mark.asyncio
async def test_delete_storage_mapping_posts_parent_without_exact_mapping(mock_hmc):
    mock_hmc.get(VIOS_PARENT_PATH).mock(return_value=httpx.Response(200, text=VIOS_PARENT))
    posted = mock_hmc.post(VIOS_POST_PATH).mock(return_value=httpx.Response(200, text=""))

    async with HMCClient(make_config()) as hmc:
        await hmc.delete_storage_mapping(VIOS_UUID, "mapping-1")

    request = posted.calls[0].request
    assert request.headers["content-type"].endswith("type=VirtualIOServer")
    root = ET.fromstring(request.content)
    ns = {"uom": "http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/"}
    assert [
        node.text
        for node in root.findall(".//uom:VirtualSCSIMapping/uom:UUID", ns)
    ] == ["mapping-10"]
    assert root.findtext("uom:ResourceMonitoringControlState", namespaces=ns) == "active"


def test_delete_storage_mapping_serializes_default_uom_namespace_in_fresh_process():
    """The shared remover must not depend on another test's namespace registry."""
    script = textwrap.dedent(
        f"""
        import asyncio
        from types import SimpleNamespace

        from hmc_mcp.client.client_storage import StorageMixin

        class FakeClient(StorageMixin):
            async def _get(self, *_args, **_kwargs):
                return {VIOS_PARENT!r}

            async def _request(self, *_args, **kwargs):
                print(kwargs["content"])
                return SimpleNamespace(status_code=200)

            async def _request_with_uuid_path_arguments(
                self, *args, uuid_path_arguments, **kwargs
            ):
                return await self._request(*args, **kwargs)

        asyncio.run(
            FakeClient().delete_storage_mapping({VIOS_UUID!r}, "mapping-1")
        )
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )

    assert "<ns0:" not in result.stdout
    assert (
        '<VirtualIOServer xmlns="http://www.ibm.com/xmlns/systems/power/'
        'firmware/uom/mc/2012_10/">' in result.stdout
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mapping_uuid", "document", "message"),
    [
        ("missing", VIOS_PARENT, "not found"),
        ("mapping-1", VIOS_PARENT.replace("mapping-10", "mapping-1"), "duplicated"),
    ],
)
async def test_delete_storage_mapping_fails_closed_without_post(
    mock_hmc, mapping_uuid, document, message
):
    mock_hmc.get(VIOS_PARENT_PATH).mock(return_value=httpx.Response(200, text=document))
    posted = mock_hmc.post(VIOS_POST_PATH).mock(return_value=httpx.Response(200, text=""))

    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError, match=message):
            await hmc.delete_storage_mapping(VIOS_UUID, mapping_uuid)
    assert not posted.called


@pytest.mark.asyncio
async def test_delete_storage_mapping_rejects_malformed_parent(mock_hmc):
    mock_hmc.get(VIOS_PARENT_PATH).mock(return_value=httpx.Response(200, text="<broken>"))
    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError, match="not valid XML"):
            await hmc.delete_storage_mapping(VIOS_UUID, "mapping-1")


@pytest.mark.asyncio
async def test_delete_storage_mapping_rejects_xml_entities(mock_hmc):
    document = '<!DOCTYPE x [<!ENTITY payload "expanded">]><x>&payload;</x>'
    mock_hmc.get(VIOS_PARENT_PATH).mock(
        return_value=httpx.Response(200, text=document)
    )

    async with HMCClient(make_config()) as hmc:
        with pytest.raises(EntitiesForbidden):
            await hmc.delete_storage_mapping(VIOS_UUID, "mapping-1")


@pytest.mark.asyncio
async def test_delete_storage_mapping_propagates_parent_post_failure(mock_hmc):
    mock_hmc.get(VIOS_PARENT_PATH).mock(return_value=httpx.Response(200, text=VIOS_PARENT))
    mock_hmc.post(VIOS_POST_PATH).mock(return_value=httpx.Response(409, text="parent changed"))
    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError) as raised:
            await hmc.delete_storage_mapping(VIOS_UUID, "mapping-1")
    assert raised.value.status_code == 409
    assert "parent changed" in str(raised.value)


@pytest.mark.asyncio
async def test_delete_storage_mapping_rejects_empty_selector(mock_hmc):
    fetched = mock_hmc.get(VIOS_PARENT_PATH).mock(return_value=httpx.Response(200, text=VIOS_PARENT))
    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError, match="must not be empty"):
            await hmc.delete_storage_mapping(VIOS_UUID, "")
    assert not fetched.called


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "document",
    [
        VIOS_PARENT.replace("AssociatedManagedSystem", "WrongAssociation"),
        VIOS_PARENT.replace(
            "<AssociatedManagedSystem",
            "<AssociatedManagedSystem href=\"/rest/api/uom/ManagedSystem/"
            f"{SYSTEM_UUID}\"/><AssociatedManagedSystem",
        ),
        VIOS_PARENT.replace(SYSTEM_UUID, "not-a-uuid"),
        VIOS_PARENT.replace(SYSTEM_UUID, "------------------------------------"),
        VIOS_PARENT.replace(SYSTEM_UUID, "a" * 36),
    ],
)
async def test_delete_storage_mapping_rejects_untrusted_system_link(
    mock_hmc, document
):
    mock_hmc.get(VIOS_PARENT_PATH).mock(return_value=httpx.Response(200, text=document))
    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError, match="AssociatedManagedSystem"):
            await hmc.delete_storage_mapping(VIOS_UUID, "mapping-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "document",
    [
        f'<feed xmlns="http://www.w3.org/2005/Atom">{VIOS_PARENT}{VIOS_PARENT}</feed>',
        VIOS_PARENT.replace(
            f"<UUID>{VIOS_UUID}</UUID>", "<UUID>wrong-vios</UUID>"
        ),
    ],
)
async def test_delete_storage_mapping_rejects_ambiguous_vios_document(
    mock_hmc, document
):
    mock_hmc.get(VIOS_PARENT_PATH).mock(return_value=httpx.Response(200, text=document))
    posted = mock_hmc.post(VIOS_POST_PATH).mock(return_value=httpx.Response(200, text=""))
    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError, match="VIOS resources|identity does not match"):
            await hmc.delete_storage_mapping(VIOS_UUID, "mapping-1")
    assert not posted.called


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "document",
    [
        VIOS_PARENT.replace(
            "<UUID>mapping-1</UUID>",
            "<UUID>mapping-1</UUID><UUID>different</UUID>",
        ),
        VIOS_PARENT.replace("<UUID>mapping-10</UUID>", ""),
        VIOS_PARENT.replace("mapping-10", "mapping-1"),
    ],
)
async def test_delete_storage_mapping_rejects_malformed_mapping_identity(
    mock_hmc, document
):
    mock_hmc.get(VIOS_PARENT_PATH).mock(return_value=httpx.Response(200, text=document))
    posted = mock_hmc.post(VIOS_POST_PATH).mock(return_value=httpx.Response(200, text=""))
    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError, match="invalid UUID|duplicated"):
            await hmc.delete_storage_mapping(VIOS_UUID, "mapping-1")
    assert not posted.called
