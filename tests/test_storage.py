"""Tests for the storage document builders."""

import pytest

from hmc_mcp.documents import (
    build_virtual_disk_document,
    build_volume_group_document,
    build_vscsi_mapping_document,
)


def test_volume_group_document():
    xml = build_volume_group_document("vg_1", ["hdisk10", "hdisk11"])
    assert "<GroupName" in xml and "vg_1" in xml
    # one container <PhysicalVolumes> + one <PhysicalVolume> per PV
    assert xml.count("<PhysicalVolume ") == 2
    assert "hdisk10" in xml and "hdisk11" in xml


def test_virtual_disk_document():
    xml = build_virtual_disk_document("lv_boot", 51200)
    assert "VolumeGroup" in xml
    assert "VirtualDisks" in xml
    assert "<DiskName" in xml and "lv_boot" in xml
    assert "<DiskCapacity" in xml and "51200" in xml


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
