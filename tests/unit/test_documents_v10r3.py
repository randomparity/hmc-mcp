"""V10R3 schema conformance of the UOM write builders (#961).

Expected values come from two sources. The first is the live fixture
tests/storage/vscsi_mapping_v10r3.xml (#940). The second is the table in RECORDED, transcribed
from the live V10R3 values recorded in the #961 body. Neither source is documentation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from defusedxml import ElementTree as DET

from hmcpctl import documents
from hmcpctl.documents.common import UOM_NS
from hmcpctl.xmlutil import localname

FIXTURE = DET.parse(Path(__file__).parents[1] / "storage" / "vscsi_mapping_v10r3.xml").getroot()


def _tree(xml: str):
    return DET.fromstring(xml.encode("utf-8"))


def _first(root, name: str):
    return next(el for el in root.iter() if localname(el.tag) == name)


def _kbx(el) -> dict[str, str]:
    return {k: v for k, v in el.attrib.items() if k in ("kb", "kxe", "schemaVersion")}


def _children(el) -> list[str]:
    return [localname(child.tag) for child in el]


def _is_subsequence(needle: list[str], haystack: list[str]) -> bool:
    it = iter(haystack)
    return all(name in it for name in needle)


def test_vscsi_adapter_matches_fixture() -> None:
    built = _tree(documents.build_vscsi_adapter_document(7, 11, slot_number=4))
    live = _first(FIXTURE, "ClientAdapter")
    assert _is_subsequence(_children(built), _children(live))
    for child in built:
        name = localname(child.tag)
        assert _kbx(child) == _kbx(_first(live, name)), name


LINK = "https://hmc.example.invalid/rest/api/uom/LogicalPartition/lpar-1"
RECORDED = [
    (
        documents.build_client_network_adapter_document(42, 3, 1, True, "02:00:00:00:00:01"),
        {"VirtualSlotNumber": "COD", "VirtualSwitchID": "ROR", "PortVLANID": "CUR",
         "MACAddress": "CUR"},
    ),
    (documents.build_volume_group_document("vg1", ["hdisk1"]), {"VolumeName": "CUR"}),
    (
        documents.build_vscsi_mapping_document("PhysicalVolume", "hdisk5", LINK),
        {"VolumeName": "CUR"},
    ),
    (documents.build_virtual_optical_mapping_document("a.iso", LINK), {"MediaName": "CUR"}),
    (
        documents.build_virtual_network_document("n1", 10, 0, tagged=True),
        {"NetworkVLANID": "COD", "VswitchID": "ROR", "TaggedNetwork": "COD"},
    ),
    (documents.build_lpar_document("p1", os_type="linux"), {"OperatingSystemType": "ROR"}),
    (documents.build_boot_order_document(["cd"]), {"PendingBootString": "UOO"}),
    (documents.build_clear_boot_order_document(), {"PendingBootString": "UOO"}),
    (
        documents.build_vfc_adapter_document(1, 2, 3),
        {"AdapterType": "ROR", "VirtualSlotNumber": "COD", "ConnectingPartitionID": "CUD",
         "ConnectingVirtualSlotNumber": "CUD"},
    ),
]


@pytest.mark.parametrize(
    ("xml", "expected"), RECORDED, ids=["-".join(expected) for _, expected in RECORDED]
)
def test_recorded_kb_values(xml: str, expected: dict[str, str]) -> None:
    root = _tree(xml)
    assert {name: _first(root, name).attrib.get("kb") for name in expected} == expected


def test_virtual_disk_create_matches_fixture() -> None:
    built = _first(_tree(documents.build_virtual_disk_element("lv1", 2048)), "VirtualDisk")
    live = _first(FIXTURE, "VirtualDisk")
    assert _kbx(built) == _kbx(live)
    names = [n for n in _children(built) if n != "Metadata"]
    assert names == ["DiskCapacity", "DiskName"]
    assert _is_subsequence(names, _children(live))
    for name in names:
        assert _kbx(_first(built, name)) == _kbx(_first(live, name)), name


def test_vscsi_mapping_matches_fixture() -> None:
    xml = documents.build_vscsi_mapping_document("VirtualDisk", "vd1", LINK, "vtscsi9")
    built = _first(_tree(xml), "VirtualSCSIMapping")
    live = _first(FIXTURE, "VirtualSCSIMapping")
    assert _kbx(built) == _kbx(live)
    assert _children(built) == [
        "Metadata", "AssociatedLogicalPartition", "Storage", "TargetDevice"
    ]
    assert _is_subsequence(_children(built), _children(live))
    link = _first(built, "AssociatedLogicalPartition")
    assert link.tag == f"{{{UOM_NS}}}AssociatedLogicalPartition"
    assert link.attrib["href"] == LINK
    assert link.attrib["rel"] == "related"
    for name in ("AssociatedLogicalPartition", "Storage", "VirtualDisk", "DiskName",
                 "TargetDevice", "LogicalVolumeVirtualTargetDevice", "TargetName"):
        assert _kbx(_first(built, name)) == _kbx(_first(live, name)), name
    assert _children(_first(built, "Storage")) == ["VirtualDisk"]
    assert _first(built, "TargetName").text == "vtscsi9"


@pytest.mark.parametrize(
    ("xml", "storage", "target"),
    [
        (
            documents.build_vscsi_mapping_document("PhysicalVolume", "hdisk5", LINK, "vt1"),
            "PhysicalVolume",
            "PhysicalVolumeVirtualTargetDevice",
        ),
        (
            documents.build_virtual_optical_mapping_document("a.iso", LINK, "vtopt1"),
            "VirtualOpticalMedia",
            "VirtualOpticalTargetDevice",
        ),
    ],
    ids=["physical-volume", "optical"],
)
def test_mapping_wrappers_and_target_device(xml: str, storage: str, target: str) -> None:
    root = _tree(xml)
    target_device = _first(root, "TargetDevice")
    assert _first(root, storage).attrib == {"schemaVersion": "V1_0"}
    assert _children(target_device) == [target]
    assert _first(root, target).attrib == {"schemaVersion": "V1_0"}
    assert not (target_device.text or "").strip()


def test_mapping_without_target_device_omits_it() -> None:
    root = _tree(documents.build_vscsi_mapping_document("VirtualDisk", "vd1", LINK))
    assert _children(_first(root, "VirtualSCSIMapping")) == [
        "Metadata", "AssociatedLogicalPartition", "Storage"
    ]


def test_volume_group_physical_volume_attributes() -> None:
    root = _tree(documents.build_volume_group_document("vg1", ["hdisk1"]))
    assert _first(root, "PhysicalVolume").attrib == {"schemaVersion": "V1_0"}


RESOURCES = documents.LparResources(
    min_memory=512, desired_memory=1024, max_memory=2048, desired_procs=0.5, desired_vcpus=1
)
DEDICATED = documents.LparResources(desired_procs=1, dedicated=True)
BUILT = {
    "vscsi-adapter": documents.build_vscsi_adapter_document(1, 2, 3),
    "vfc-adapter": documents.build_vfc_adapter_document(1, 2, 3),
    "network-adapter": documents.build_client_network_adapter_document(
        1, 2, 0, True, "02:00:00:00:00:01"
    ),
    "volume-group": documents.build_volume_group_document("vg1", ["hdisk1"]),
    "virtual-disk": documents.build_virtual_disk_element("lv1", 1024),
    "vscsi-mapping": documents.build_vscsi_mapping_document(
        "PhysicalVolume", "hdisk1", LINK, "vt1"
    ),
    "optical-mapping": documents.build_virtual_optical_mapping_document("a.iso", LINK, "vt2"),
    "virtual-network": documents.build_virtual_network_document("n1", 10, 0, LINK),
    "media-repository-delete": documents.build_media_repository_delete_document("vg1"),
    "optical-media-delete": documents.build_virtual_optical_media_delete_document(
        "a.iso", "vg1"
    ),
    "brokered-file": documents.build_brokered_file_document("a.iso"),
    "linked-optical-media": documents.build_linked_optical_media_document("a.iso", LINK),
    "lpar-shared": documents.build_lpar_document("p1", resources=RESOURCES, os_type="linux"),
    "lpar-dedicated": documents.build_lpar_document("p1", resources=DEDICATED),
    "vios": documents.build_vios_document("v1"),
    "dlpar-mem": documents.build_dlpar_mem_document(RESOURCES),
    "dlpar-proc": documents.build_dlpar_proc_document(RESOURCES),
    "boot-order": documents.build_boot_order_document(["cd"]),
    "clear-boot-order": documents.build_clear_boot_order_document(),
}
# No live evidence records whether V10R3 requires schemaVersion on these; left unchanged (#961).
PROCESSOR_WRAPPERS_UNVERIFIED = frozenset(
    {"PartitionProcessorConfiguration", "SharedProcessorConfiguration",
     "DedicatedProcessorConfiguration"}
)


@pytest.mark.parametrize("xml", BUILT.values(), ids=BUILT.keys())
def test_metadata_elements_carry_schema_version(xml: str) -> None:
    missing = [
        localname(el.tag)
        for el in _tree(xml).iter()
        if "Metadata" in _children(el)
        and "schemaVersion" not in el.attrib
        and localname(el.tag) not in PROCESSOR_WRAPPERS_UNVERIFIED
    ]
    assert missing == []
