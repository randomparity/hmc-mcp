"""Tool-layer tests for the virtual adapter / storage / SSP tools.

The document builders are covered in this dir's other tests; these tests call
the actual ``@mcp.tool`` functions in ``server_tools.storage`` against the respx
``mock_hmc`` router so the argument->URL and argument->XML mapping in the
tool bodies is exercised — the layer the client tests skip.  This mirrors
``tests/app/test_server_tools.py`` for the storage domain.
"""

from __future__ import annotations

from unittest.mock import ANY, AsyncMock, patch

import httpx
import pytest
from conftest import JOB_ENTRY

from hmc_mcp.client.client_adapters import ADAPTER_TYPES
from hmc_mcp.server_tools.adapters import (
    hmc_add_network_adapter as hmc_add_network_adapter,
)
from hmc_mcp.server_tools.adapters import (
    hmc_add_vfc_adapter as hmc_add_vfc_adapter,
)
from hmc_mcp.server_tools.adapters import (
    hmc_add_vscsi_adapter as hmc_add_vscsi_adapter,
)
from hmc_mcp.server_tools.adapters import (
    hmc_delete_adapter as hmc_delete_adapter,
)
from hmc_mcp.server_tools.adapters import (
    hmc_list_adapters as hmc_list_adapters,
)
from hmc_mcp.server_tools.storage import (
    hmc_create_logical_unit as hmc_create_logical_unit,
)
from hmc_mcp.server_tools.storage import (
    hmc_create_media_repository as hmc_create_media_repository,
)
from hmc_mcp.server_tools.storage import (
    hmc_create_optical_media as hmc_create_optical_media,
)
from hmc_mcp.server_tools.storage import (
    hmc_create_virtual_disk as hmc_create_virtual_disk,
)
from hmc_mcp.server_tools.storage import (
    hmc_create_volume_group as hmc_create_volume_group,
)
from hmc_mcp.server_tools.storage import (
    hmc_delete_logical_unit as hmc_delete_logical_unit,
)
from hmc_mcp.server_tools.storage import (
    hmc_delete_media_repository as hmc_delete_media_repository,
)
from hmc_mcp.server_tools.storage import hmc_detach_storage_mapping
from hmc_mcp.server_tools.storage import (
    hmc_get_shared_storage_pool as hmc_get_shared_storage_pool,
)
from hmc_mcp.server_tools.storage import (
    hmc_list_clusters as hmc_list_clusters,
)
from hmc_mcp.server_tools.storage import (
    hmc_list_shared_storage_pools as hmc_list_shared_storage_pools,
)
from hmc_mcp.server_tools.storage import (
    hmc_list_volume_groups as hmc_list_volume_groups,
)
from hmc_mcp.server_tools.storage import (
    hmc_map_storage_to_lpar as hmc_map_storage_to_lpar,
)


@pytest.fixture(autouse=True)
def _authorize_lpar_mutations(monkeypatch):
    async def authorize(_hmc, lpar, _system, **_kwargs):
        return lpar

    monkeypatch.setattr(
        "hmc_mcp.operations.adapters.resolve_and_authorize_lpar_mutation", authorize
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.storage.resolve_and_authorize_lpar_mutation", authorize
    )

LPAR_UUID = "00000000-0000-0000-0000-000000000002"
VIOS_UUID = "00000000-0000-0000-0000-000000000003"
VG_UUID = "vg-uuid-0001"
ADAPTER_UUID = "adapter-uuid-0001"
CLUSTER_UUID = "cluster-uuid-0001"
SSP_UUID = "ssp-uuid-0001"
SYSTEM_UUID = "00000000-0000-0000-0000-000000000004"


def _hmc_env(monkeypatch) -> None:
    """Set env vars so HMCConfig() succeeds inside the tool."""
    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "abc123")


def _feed(uuid: str, rtype: str, **fields: str) -> str:
    """A single-resource Atom feed; {fields} render as resource elements."""
    body = "\n".join(
        f'        <{name} xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">{value}</{name}>'
        for name, value in fields.items()
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:{uuid}</id>
    <title>{rtype}:{uuid}</title>
    <link rel="SELF" href="https://hmc.test:12443/rest/api/uom/{rtype}/{uuid}"/>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <{rtype} xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
{body}
      </{rtype}>
    </content>
  </entry>
</feed>
"""


# ---------------------------------------------------------------------- #
# hmc_list_adapters / hmc_add_*_adapter / hmc_delete_adapter
# ---------------------------------------------------------------------- #


def test_list_adapters_defaults_to_network(monkeypatch, mock_hmc):
    """hmc_list_adapters GETs ClientNetworkAdapter by default."""
    _hmc_env(monkeypatch)
    mock_hmc.get(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/ClientNetworkAdapter"
    ).mock(
        return_value=httpx.Response(
            200,
            text=_feed(
                ADAPTER_UUID, "ClientNetworkAdapter", MACAddress="00:11:22:33:44:55"
            ),
        )
    )
    result = hmc_list_adapters(LPAR_UUID)
    assert result[0]["UUID"] == ADAPTER_UUID
    assert result[0]["Resource"]["MACAddress"] == "00:11:22:33:44:55"


def test_list_adapters_vscsi_type(monkeypatch, mock_hmc):
    """hmc_list_adapters honors a non-default adapter_type."""
    _hmc_env(monkeypatch)
    route = mock_hmc.get(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/VirtualSCSIClientAdapter"
    ).mock(
        return_value=httpx.Response(
            200, text=_feed(ADAPTER_UUID, "VirtualSCSIClientAdapter")
        )
    )
    hmc_list_adapters(LPAR_UUID, adapter_type="VirtualSCSIClientAdapter")
    assert route.called


def test_all_adapter_types_reach_the_matching_resource(monkeypatch, mock_hmc):
    _hmc_env(monkeypatch)
    for adapter_type in ADAPTER_TYPES:
        route = mock_hmc.get(
            f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/{adapter_type}"
        ).mock(return_value=httpx.Response(200, text=""))
        hmc_list_adapters(LPAR_UUID, adapter_type=adapter_type)
        assert route.called


def test_invalid_adapter_type_fails_before_transport(monkeypatch, mock_hmc):
    _hmc_env(monkeypatch)
    with pytest.raises(ValueError, match="adapter_type"):
        hmc_list_adapters(LPAR_UUID, adapter_type="UnknownAdapter")
    assert not mock_hmc.calls


def test_detach_storage_mapping_posts_parent_vios(monkeypatch, mock_hmc):
    _hmc_env(monkeypatch)
    parent = f"""<VirtualIOServer
      xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <UUID>{VIOS_UUID}</UUID>
      <AssociatedManagedSystem href="/rest/api/uom/ManagedSystem/{SYSTEM_UUID}"/>
      <VirtualSCSIMappings>
        <VirtualSCSIMapping><UUID>map-1</UUID></VirtualSCSIMapping>
        <VirtualSCSIMapping><UUID>map-2</UUID></VirtualSCSIMapping>
      </VirtualSCSIMappings>
    </VirtualIOServer>"""
    inventory = f"""<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:uom="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <entry><content><VirtualIOServer><VirtualSCSIMappings>
        <VirtualSCSIMapping><UUID>map-1</UUID>
          <AssociatedLogicalPartition
            href="/rest/api/uom/LogicalPartition/{LPAR_UUID}"/>
        </VirtualSCSIMapping>
      </VirtualSCSIMappings></VirtualIOServer></content></entry>
    </feed>"""
    mock_hmc.get(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}?group=ViosSCSIMapping"
    ).mock(return_value=httpx.Response(200, text=inventory))
    mock_hmc.get(f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}").mock(
        return_value=httpx.Response(200, text=parent)
    )
    posted = mock_hmc.post(
        f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/VirtualIOServer/{VIOS_UUID}"
    ).mock(return_value=httpx.Response(200, text=""))

    guard = AsyncMock(return_value=LPAR_UUID)
    with patch(
        "hmc_mcp.operations.storage.resolve_and_authorize_lpar_mutation", new=guard
    ):
        assert hmc_detach_storage_mapping(VIOS_UUID, "map-1") == "map-1"

    guard.assert_awaited_once_with(
        ANY,
        LPAR_UUID,
        None,
        ownership_override=False,
    )
    assert posted.called
    assert "map-1" not in posted.calls.last.request.content.decode()
    assert "map-2" in posted.calls.last.request.content.decode()


def test_add_network_adapter_builds_xml(monkeypatch, mock_hmc):
    """hmc_add_network_adapter maps its args into a ClientNetworkAdapter doc."""
    _hmc_env(monkeypatch)
    route = mock_hmc.put(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/ClientNetworkAdapter"
    ).mock(
        return_value=httpx.Response(
            201, text=_feed(ADAPTER_UUID, "ClientNetworkAdapter")
        )
    )
    result = hmc_add_network_adapter(
        LPAR_UUID,
        port_vlan_id=42,
        slot_number=3,
        virtual_switch_id=1,
        tagged=True,
        mac_address="00:11:22:33:44:55",
    )
    assert route.called
    body = route.calls.last.request.content.decode()
    assert "<ClientNetworkAdapter" in body
    assert '<PortVLANID kb="CUD" kxe="false">42</PortVLANID>' in body
    assert '<VirtualSlotNumber kb="CUD" kxe="false">3</VirtualSlotNumber>' in body
    assert '<VirtualSwitchID kb="CUD" kxe="false">1</VirtualSwitchID>' in body
    assert '<IsTaggedVLAN kb="CUD" kxe="false">true</IsTaggedVLAN>' in body
    assert '<MACAddress kb="CUD" kxe="false">00:11:22:33:44:55</MACAddress>' in body
    assert result["UUID"] == ADAPTER_UUID


def test_add_vscsi_adapter_builds_xml(monkeypatch, mock_hmc):
    """hmc_add_vscsi_adapter maps vios_partition_id/vios_slot into the doc."""
    _hmc_env(monkeypatch)
    route = mock_hmc.put(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/VirtualSCSIClientAdapter"
    ).mock(
        return_value=httpx.Response(
            201, text=_feed(ADAPTER_UUID, "VirtualSCSIClientAdapter")
        )
    )
    hmc_add_vscsi_adapter(LPAR_UUID, vios_partition_id=7, vios_slot=11, slot_number=4)
    body = route.calls.last.request.content.decode()
    assert "<VirtualSCSIClientAdapter" in body
    assert (
        '<RemoteLogicalPartitionID kb="CUD" kxe="false">7</RemoteLogicalPartitionID>'
        in body
    )
    assert '<RemoteSlotNumber kb="CUD" kxe="false">11</RemoteSlotNumber>' in body
    assert '<VirtualSlotNumber kb="CUD" kxe="false">4</VirtualSlotNumber>' in body


def test_add_vfc_adapter_builds_xml(monkeypatch, mock_hmc):
    """hmc_add_vfc_adapter maps vios_partition_id/vios_slot into the doc."""
    _hmc_env(monkeypatch)
    route = mock_hmc.put(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/VirtualFibreChannelClientAdapter"
    ).mock(
        return_value=httpx.Response(
            201, text=_feed(ADAPTER_UUID, "VirtualFibreChannelClientAdapter")
        )
    )
    hmc_add_vfc_adapter(LPAR_UUID, vios_partition_id=7, vios_slot=11)
    body = route.calls.last.request.content.decode()
    assert "<VirtualFibreChannelClientAdapter" in body
    assert (
        '<ConnectingPartitionID kb="CUD" kxe="false">7</ConnectingPartitionID>' in body
    )
    assert (
        '<ConnectingVirtualSlotNumber kb="CUD" kxe="false">11</ConnectingVirtualSlotNumber>'
        in body
    )


def test_delete_adapter_returns_confirmation(monkeypatch, mock_hmc):
    """hmc_delete_adapter DELETEs the adapter and returns a confirmation."""
    _hmc_env(monkeypatch)
    route = mock_hmc.delete(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/VirtualSCSIClientAdapter/{ADAPTER_UUID}"
    ).mock(return_value=httpx.Response(204))
    result = hmc_delete_adapter(LPAR_UUID, "VirtualSCSIClientAdapter", ADAPTER_UUID)
    assert route.called
    assert result == f"Deleted VirtualSCSIClientAdapter {ADAPTER_UUID} from {LPAR_UUID}"


# ---------------------------------------------------------------------- #
# Volume groups / virtual disks / storage mapping
# ---------------------------------------------------------------------- #


def test_list_volume_groups(monkeypatch, mock_hmc):
    """hmc_list_volume_groups GETs the VIOS VolumeGroup collection."""
    _hmc_env(monkeypatch)
    mock_hmc.get(f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup").mock(
        return_value=httpx.Response(
            200, text=_feed(VG_UUID, "VolumeGroup", GroupName="vg_rootvg")
        )
    )
    result = hmc_list_volume_groups(VIOS_UUID)
    assert result[0]["UUID"] == VG_UUID
    assert result[0]["Resource"]["GroupName"] == "vg_rootvg"


def test_create_volume_group_builds_xml(monkeypatch, mock_hmc):
    """hmc_create_volume_group PUTs a VolumeGroup doc with the PV list."""
    _hmc_env(monkeypatch)
    route = mock_hmc.put(f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup").mock(
        return_value=httpx.Response(201, text=_feed(VG_UUID, "VolumeGroup"))
    )
    hmc_create_volume_group(VIOS_UUID, "vg_data", ["hdisk10", "hdisk11"])
    body = route.calls.last.request.content.decode()
    assert '<GroupName kb="CUD" kxe="false">vg_data</GroupName>' in body
    assert body.count("<PhysicalVolume ") == 2
    assert "hdisk10" in body and "hdisk11" in body


def test_create_virtual_disk_builds_xml(monkeypatch, mock_hmc):
    """hmc_create_virtual_disk POSTs a VolumeGroup doc with a VirtualDisk."""
    _hmc_env(monkeypatch)
    route = mock_hmc.post(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup/{VG_UUID}"
    ).mock(return_value=httpx.Response(201, text=_feed(VG_UUID, "VolumeGroup")))
    hmc_create_virtual_disk(VIOS_UUID, VG_UUID, "lv_boot", 51200)
    body = route.calls.last.request.content.decode()
    assert "<VirtualDisks" in body
    assert '<DiskName kb="CUD" kxe="false">lv_boot</DiskName>' in body
    assert '<DiskCapacity kb="CUD" kxe="false">51200</DiskCapacity>' in body


def test_map_storage_reorders_virtual_disk_default(monkeypatch, mock_hmc):
    """hmc_map_storage_to_lpar maps the default VirtualDisk storage_kind."""
    _hmc_env(monkeypatch)
    route = mock_hmc.post(f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}").mock(
        return_value=httpx.Response(201, text=_feed(VIOS_UUID, "VirtualIOServer"))
    )
    hmc_map_storage_to_lpar(VIOS_UUID, "lv_boot", LPAR_UUID)
    body = route.calls.last.request.content.decode()
    assert "<VirtualSCSIMapping" in body
    # storage_kind lands as the element name; storage_name is DiskName.
    assert "<VirtualDisk kb=" in body
    assert '<DiskName kb="CUD" kxe="false">lv_boot</DiskName>' in body
    assert f"/rest/api/uom/LogicalPartition/{LPAR_UUID}" in body


def test_map_storage_physical_volume_with_target_device(monkeypatch, mock_hmc):
    """PhysicalVolume storage_kind uses VolumeName and emits TargetDevice."""
    _hmc_env(monkeypatch)
    route = mock_hmc.post(f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}").mock(
        return_value=httpx.Response(201, text=_feed(VIOS_UUID, "VirtualIOServer"))
    )
    hmc_map_storage_to_lpar(
        VIOS_UUID,
        "hdisk5",
        LPAR_UUID,
        storage_kind="PhysicalVolume",
        target_device="vtscsi0",
    )
    body = route.calls.last.request.content.decode()
    assert "<PhysicalVolume kb=" in body
    assert '<VolumeName kb="CUD" kxe="false">hdisk5</VolumeName>' in body
    assert '<TargetDevice kb="CUD" kxe="false">vtscsi0</TargetDevice>' in body
    assert f"/rest/api/uom/LogicalPartition/{LPAR_UUID}" in body


def test_map_storage_invalid_kind_raises(monkeypatch, mock_hmc):
    """An invalid storage_kind is rejected before any request is sent."""
    _hmc_env(monkeypatch)
    with pytest.raises(
        ValueError, match="storage_kind must be PhysicalVolume or VirtualDisk"
    ):
        hmc_map_storage_to_lpar(VIOS_UUID, "lv_boot", LPAR_UUID, storage_kind="Bogus")


# ---------------------------------------------------------------------- #
# Virtual media repository
# ---------------------------------------------------------------------- #

_BARE_VG_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <content>
      <VolumeGroup xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <GroupName>clientvg1</GroupName>
        <VirtualDisks/>
      </VolumeGroup>
    </content>
  </entry>
</feed>"""

_VMLIB_VG_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <content>
      <VolumeGroup xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <GroupName>clientvg1</GroupName>
        <MediaRepositories>
          <VirtualMediaRepository>
            <RepositoryName>VMLibrary</RepositoryName>
            <RepositorySize>7000</RepositorySize>
          </VirtualMediaRepository>
        </MediaRepositories>
      </VolumeGroup>
    </content>
  </entry>
</feed>"""


def test_create_media_repository_builds_xml(monkeypatch, mock_hmc):
    """hmc_create_media_repository GETs the VG then POSTs a VMLibrary repository doc.

    The read-modify-write pattern is used on V10R3 firmware; no kb= attributes
    are emitted by ET.tostring() — assertions check for element/text presence only.
    """
    _hmc_env(monkeypatch)
    vg_path = f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup/{VG_UUID}"
    mock_hmc.get(vg_path).mock(return_value=httpx.Response(200, text=_BARE_VG_FEED))
    route = mock_hmc.post(vg_path).mock(
        return_value=httpx.Response(201, text=_feed(VG_UUID, "VolumeGroup"))
    )
    hmc_create_media_repository(VIOS_UUID, VG_UUID, 40960)
    body = route.calls.last.request.content.decode()
    assert "VirtualMediaRepository" in body
    assert "VMLibrary" in body
    assert "40960" in body


def test_create_optical_media_builds_xml(monkeypatch, mock_hmc):
    """hmc_create_optical_media GETs the VG then POSTs a blank-media doc.

    No kb= attributes are emitted; assertions are element/text only.
    """
    _hmc_env(monkeypatch)
    vg_path = f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup/{VG_UUID}"
    mock_hmc.get(vg_path).mock(return_value=httpx.Response(200, text=_VMLIB_VG_FEED))
    route = mock_hmc.post(vg_path).mock(
        return_value=httpx.Response(201, text=_feed(VG_UUID, "VolumeGroup"))
    )
    hmc_create_optical_media(VIOS_UUID, VG_UUID, "aix.iso", 4096)
    body = route.calls.last.request.content.decode()
    assert "VirtualOpticalMedia" in body
    assert "aix.iso" in body
    assert "4096" in body
    assert "MountType" in body


def test_delete_media_repository_returns_confirmation(monkeypatch, mock_hmc):
    """hmc_delete_media_repository GETs the VG, removes VMLibrary, POSTs, and confirms.

    The GET is used twice: once by list_optical_media (safety check) and once by
    delete_media_repository (read-modify-write). Both calls hit the same mock.
    """
    _hmc_env(monkeypatch)
    vg_path = f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup/{VG_UUID}"
    # Both list_optical_media and delete_media_repository GET the same path.
    mock_hmc.get(vg_path).mock(return_value=httpx.Response(200, text=_VMLIB_VG_FEED))
    route = mock_hmc.post(vg_path).mock(
        return_value=httpx.Response(201, text=_feed(VG_UUID, "VolumeGroup"))
    )
    result = hmc_delete_media_repository(VIOS_UUID, VG_UUID)
    # POST body must not contain MediaRepositories (it was removed by the mutation).
    body = route.calls.last.request.content.decode()
    assert "MediaRepositories" not in body
    assert result == f"Deleted media repository from VolumeGroup {VG_UUID}"


# ---------------------------------------------------------------------- #
# Clusters / Shared Storage Pools
# ---------------------------------------------------------------------- #


def test_list_clusters(monkeypatch, mock_hmc):
    """hmc_list_clusters GETs the Cluster collection."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/Cluster").mock(
        return_value=httpx.Response(200, text=_feed(CLUSTER_UUID, "Cluster"))
    )
    result = hmc_list_clusters()
    assert result[0]["UUID"] == CLUSTER_UUID


def test_shared_storage_pools_lists_all(monkeypatch, mock_hmc):
    """hmc_list_shared_storage_pools() GETs the SharedStoragePool collection."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/SharedStoragePool").mock(
        return_value=httpx.Response(200, text=_feed(SSP_UUID, "SharedStoragePool"))
    )
    result = hmc_list_shared_storage_pools()
    assert result[0]["UUID"] == SSP_UUID


def test_get_shared_storage_pool_gets_one(monkeypatch, mock_hmc):
    """hmc_get_shared_storage_pool GETs one SSP by UUID."""
    _hmc_env(monkeypatch)
    mock_hmc.get(f"/rest/api/uom/SharedStoragePool/{SSP_UUID}").mock(
        return_value=httpx.Response(200, text=_feed(SSP_UUID, "SharedStoragePool"))
    )
    result = hmc_get_shared_storage_pool(SSP_UUID)
    assert result["UUID"] == SSP_UUID


def test_create_logical_unit_submits_job(monkeypatch, mock_hmc):
    """hmc_create_logical_unit PUTs a CreateLogicalUnit job with the params."""
    _hmc_env(monkeypatch)
    route = mock_hmc.put(
        f"/rest/api/uom/Cluster/{CLUSTER_UUID}/do/CreateLogicalUnit"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))
    result = hmc_create_logical_unit(
        CLUSTER_UUID, "lu_data", 100, lu_type="THICK", device_type="VirtualIO_Image"
    )
    assert route.called
    body = route.calls.last.request.content.decode()
    assert "CreateLogicalUnit</OperationName>" in body
    # Params render as ParameterName/ParameterValue pairs.
    assert '<ParameterName kb="ROR" kxe="false">LUName</ParameterName>' in body
    assert '<ParameterValue kb="CUR" kxe="false">lu_data</ParameterValue>' in body
    assert '<ParameterName kb="ROR" kxe="false">LUSize</ParameterName>' in body
    assert '<ParameterValue kb="CUR" kxe="false">100</ParameterValue>' in body
    assert '<ParameterValue kb="CUR" kxe="false">THICK</ParameterValue>' in body
    assert (
        '<ParameterValue kb="CUR" kxe="false">VirtualIO_Image</ParameterValue>' in body
    )
    assert "ClonedFrom" not in body
    assert result["Resource"]["JobID"] == "job-uuid-999"


@pytest.mark.parametrize(
    "lu_type,device_type,match",
    [
        ("SPARSE", "VirtualIO_Disk", "lu_type"),
        ("THIN", "PhysicalDisk", "device_type"),
    ],
)
def test_create_logical_unit_rejects_invalid_types_before_transport(
    monkeypatch, mock_hmc, lu_type, device_type, match
):
    _hmc_env(monkeypatch)
    with pytest.raises(ValueError, match=match):
        hmc_create_logical_unit(
            CLUSTER_UUID,
            "lu_data",
            100,
            lu_type=lu_type,
            device_type=device_type,
        )
    assert not mock_hmc.calls


def test_create_logical_unit_with_clone(monkeypatch, mock_hmc):
    """cloned_from adds a ClonedFrom param to the job body."""
    _hmc_env(monkeypatch)
    route = mock_hmc.put(
        f"/rest/api/uom/Cluster/{CLUSTER_UUID}/do/CreateLogicalUnit"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))
    hmc_create_logical_unit(CLUSTER_UUID, "lu_clone", 50, cloned_from="udid-1234")
    body = route.calls.last.request.content.decode()
    assert '<ParameterName kb="ROR" kxe="false">ClonedFrom</ParameterName>' in body
    assert '<ParameterValue kb="CUR" kxe="false">udid-1234</ParameterValue>' in body


def test_delete_logical_unit_submits_job(monkeypatch, mock_hmc):
    """hmc_delete_logical_unit PUTs a DeleteLogicalUnit job by UDID."""
    _hmc_env(monkeypatch)
    route = mock_hmc.put(
        f"/rest/api/uom/Cluster/{CLUSTER_UUID}/do/DeleteLogicalUnit"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))
    result = hmc_delete_logical_unit(CLUSTER_UUID, "udid-1234")
    body = route.calls.last.request.content.decode()
    assert "DeleteLogicalUnit</OperationName>" in body
    assert '<ParameterName kb="ROR" kxe="false">LogicalUnitUDID</ParameterName>' in body
    assert '<ParameterValue kb="CUR" kxe="false">udid-1234</ParameterValue>' in body
    assert result["Resource"]["JobID"] == "job-uuid-999"


# ---------------------------------------------------------------------- #
# wait=True path: create/delete LU blocks until job reaches terminal state
# ---------------------------------------------------------------------- #

JOB_ENTRY_COMPLETED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:job-uuid-999</id>
  <title>Job</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <Job xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <JobID>job-uuid-999</JobID>
      <Status>COMPLETED</Status>
    </Job>
  </content>
</entry>
"""


def test_create_logical_unit_wait_true_polls_to_completion(monkeypatch, mock_hmc):
    """hmc_create_logical_unit(wait=True) submits then polls until COMPLETED."""
    _hmc_env(monkeypatch)
    submit_route = mock_hmc.put(
        f"/rest/api/uom/Cluster/{CLUSTER_UUID}/do/CreateLogicalUnit"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))
    poll_route = mock_hmc.get("/rest/api/uom/jobs/job-uuid-999").mock(
        return_value=httpx.Response(200, text=JOB_ENTRY_COMPLETED)
    )
    result = hmc_create_logical_unit(
        CLUSTER_UUID, "lu_data", 100, wait=True, timeout_seconds=60, poll_interval=1
    )
    assert submit_route.called
    assert poll_route.called
    assert result["Resource"]["Status"] == "COMPLETED"


def test_delete_logical_unit_wait_true_polls_to_completion(monkeypatch, mock_hmc):
    """hmc_delete_logical_unit(wait=True) submits then polls until COMPLETED."""
    _hmc_env(monkeypatch)
    submit_route = mock_hmc.put(
        f"/rest/api/uom/Cluster/{CLUSTER_UUID}/do/DeleteLogicalUnit"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))
    poll_route = mock_hmc.get("/rest/api/uom/jobs/job-uuid-999").mock(
        return_value=httpx.Response(200, text=JOB_ENTRY_COMPLETED)
    )
    result = hmc_delete_logical_unit(
        CLUSTER_UUID, "udid-1234", wait=True, timeout_seconds=60, poll_interval=1
    )
    assert submit_route.called
    assert poll_route.called
    assert result["Resource"]["Status"] == "COMPLETED"
