"""Tests for the storage document builders."""

import pytest

from hmcpctl.documents import (
    build_virtual_disk_element,
    build_volume_group_document,
    build_vscsi_mapping_document,
)


def test_volume_group_document():
    xml = build_volume_group_document("vg_1", ["hdisk10", "hdisk11"])
    assert "<GroupName" in xml and "vg_1" in xml
    # one container <PhysicalVolumes> + one <PhysicalVolume> per PV
    assert xml.count("<PhysicalVolume ") == 2
    assert "hdisk10" in xml and "hdisk11" in xml


def test_virtual_disk_element():
    xml = build_virtual_disk_element("lv_boot", 51200)
    assert xml.startswith("<VirtualDisk ")
    assert "<DiskName" in xml and "lv_boot" in xml
    assert "<DiskCapacity" in xml and "50" in xml


@pytest.mark.parametrize("capacity_mib", [0, -1024, 1, 1025])
def test_virtual_disk_element_rejects_non_integral_gib(capacity_mib: int) -> None:
    with pytest.raises(ValueError, match="positive multiple of 1024"):
        build_virtual_disk_element("lv_boot", capacity_mib)


def test_virtual_disk_element_accepts_15_character_name() -> None:
    assert "lv_fifteen_char" in build_virtual_disk_element("lv_fifteen_char", 1024)


def test_virtual_disk_element_counts_the_unescaped_name() -> None:
    assert "a&amp;b_fifteen_cha" in build_virtual_disk_element("a&b_fifteen_cha", 1024)


def test_virtual_disk_element_rejects_16_character_name() -> None:
    with pytest.raises(ValueError, match="15 characters"):
        build_virtual_disk_element("lv_sixteen_chars", 1024)


def test_vscsi_mapping_virtual_disk():
    xml = build_vscsi_mapping_document(
        "VirtualDisk", "lv_boot", "https://hmc:12443/rest/api/uom/LogicalPartition/lpar-uuid"
    )
    assert "VirtualSCSIMapping" in xml
    assert "<VirtualDisk" in xml
    assert "<DiskName" in xml and "lv_boot" in xml
    assert "AssociatedLogicalPartition" in xml
    assert "LogicalPartition/lpar-uuid" in xml
    assert 'rel="related"' in xml


def test_vscsi_mapping_physical_volume():
    xml = build_vscsi_mapping_document(
        "PhysicalVolume", "hdisk5", "https://hmc/rest/api/uom/LogicalPartition/lpar-1",
        target_device="vtscsi0",
    )
    assert "<PhysicalVolume" in xml
    assert "<VolumeName" in xml and "hdisk5" in xml
    assert "TargetDevice" in xml and "vtscsi0" in xml


def test_vscsi_mapping_invalid_kind():
    with pytest.raises(ValueError, match="storage_kind"):
        build_vscsi_mapping_document("Banana", "x", "http://link")
