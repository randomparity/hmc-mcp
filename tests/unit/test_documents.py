"""Tests for the LogicalPartition create/modify document builder."""

import pytest

from hmc_mcp.documents import (
    LparResources,
    PARTITION_TYPES,
    SHARING_MODES,
    TASK_ROLES,
    build_hmc_user_document,
    build_lpar_document,
)


def test_minimal_create_document():
    xml = build_lpar_document(name="test")
    assert "<PartitionName" in xml and "test" in xml
    assert "AIX/Linux" in xml
    # No memory/proc blocks when nothing is requested
    assert "PartitionMemoryConfiguration" not in xml
    assert "PartitionProcessorConfiguration" not in xml


def test_memory_config():
    xml = build_lpar_document(
        name="m",
        resources=LparResources(
            min_memory=256, desired_memory=512, max_memory=1024
        ),
    )
    assert "<DesiredMemory" in xml and "512" in xml
    assert "<MaximumMemory" in xml and "1024" in xml
    assert "<MinimumMemory" in xml and "256" in xml


def test_shared_processor_config():
    xml = build_lpar_document(
        name="s",
        resources=LparResources(
            desired_procs=0.5,
            max_procs=2.0,
            desired_vcpus=1,
            max_vcpus=2,
            dedicated=False,
            uncapped=True,
        ),
    )
    assert "<HasDedicatedProcessors" in xml and "false" in xml
    assert "SharedProcessorConfiguration" in xml
    assert "DesiredProcessingUnits" in xml and "0.5" in xml
    assert "DesiredVirtualProcessors" in xml
    assert "uncapped" in xml


def test_processor_config_mode_unchanged_when_unspecified():
    """A proc modify without dedicated/uncapped must not flip the sharing mode."""
    xml = build_lpar_document(
        name="m",
        resources=LparResources(desired_procs=0.5, desired_vcpus=1),
    )
    assert "SharedProcessorConfiguration" in xml
    assert "DesiredProcessingUnits" in xml and "0.5" in xml
    # Mode fields are omitted so the HMC keeps the current mode.
    assert "HasDedicatedProcessors" not in xml
    assert "SharingMode" not in xml
    assert "UncappedWeight" not in xml


def test_dedicated_processor_config():
    xml = build_lpar_document(
        name="d",
        resources=LparResources(
            dedicated=True, min_procs=2, desired_procs=2, max_procs=4
        ),
    )
    assert "DedicatedProcessorConfiguration" in xml
    assert "<DesiredProcessors" in xml and "2" in xml
    assert "<MaximumProcessors" in xml and "4" in xml
    assert "true" in xml  # HasDedicatedProcessors


@pytest.mark.parametrize("dedicated", [True, False])
def test_invalid_sharing_mode_is_rejected_before_xml(dedicated):
    illegal = "uncapped</SharingMode><Injected>true</Injected>"

    with pytest.raises(ValueError) as exc_info:
        build_lpar_document(
            name="bad",
            resources=LparResources(
                dedicated=dedicated, desired_procs=1, sharing_mode=illegal
            ),
        )

    message = str(exc_info.value)
    assert "sharing_mode" in message
    assert all(value in message for value in SHARING_MODES)
    assert illegal not in message


@pytest.mark.parametrize("sharing_mode", SHARING_MODES)
def test_all_sharing_modes_serialize_unchanged(sharing_mode):
    dedicated = sharing_mode not in {"capped", "uncapped"}
    xml = build_lpar_document(
        name="ok",
        resources=LparResources(
            dedicated=dedicated, desired_procs=1, sharing_mode=sharing_mode
        ),
    )

    assert f">{sharing_mode}</SharingMode>" in xml


def test_partition_id_and_type():
    xml = build_lpar_document(name="x", partition_id=25, partition_type="OS400")
    assert "<PartitionID" in xml and "25" in xml
    assert "OS400" in xml


def test_invalid_partition_type():
    with pytest.raises(ValueError, match="partition_type"):
        build_lpar_document(name="bad", partition_type="Windows")


def test_invalid_os_type():
    with pytest.raises(ValueError, match="os_type"):
        build_lpar_document(name="bad", os_type="windows")


def test_invalid_keylock():
    with pytest.raises(ValueError, match="keylock"):
        build_lpar_document(name="bad", keylock="turbo")


def test_all_partition_types_accepted():
    for pt in PARTITION_TYPES:
        build_lpar_document(name="ok", partition_type=pt)


def test_all_task_roles_serialize_unchanged():
    for taskrole in TASK_ROLES:
        xml = build_hmc_user_document(username="operator", taskrole=taskrole)
        assert f">{taskrole}</TaskRole>" in xml


def test_invalid_task_role_is_rejected():
    with pytest.raises(ValueError, match="taskrole"):
        build_hmc_user_document(username="operator", taskrole="administrator")


def test_modify_document_omits_name_when_none():
    xml = build_lpar_document(
        name=None, resources=LparResources(desired_memory=2048)
    )
    assert "PartitionName" not in xml
    assert "2048" in xml


def test_os_type_emitted():
    xml = build_lpar_document(name="mypart", os_type="aix")
    assert "<OperatingSystemType" in xml
    assert "aix" in xml


def test_keylock_emitted():
    xml = build_lpar_document(name="mypart", keylock="normal")
    assert "<KeylockPosition" in xml
    assert "normal" in xml


def test_max_virtual_slots_emitted():
    xml = build_lpar_document(name="mypart", max_virtual_slots=64)
    assert "<MaximumVirtualIoSlots" in xml
    assert "64" in xml


def test_all_three_new_fields_together():
    xml = build_lpar_document(
        name="mypart",
        os_type="linux",
        keylock="manual",
        max_virtual_slots=32,
    )
    assert "<OperatingSystemType" in xml and "linux" in xml
    assert "<KeylockPosition" in xml and "manual" in xml
    assert "<MaximumVirtualIoSlots" in xml and "32" in xml


def test_new_fields_default_to_none_backward_compat():
    xml = build_lpar_document(name="mypart")
    assert "OperatingSystemType" not in xml
    assert "KeylockPosition" not in xml
    assert "MaximumVirtualIoSlots" not in xml


def test_ibmi_os_type():
    xml = build_lpar_document(name="ibmi-part", os_type="ibmi")
    assert "<OperatingSystemType" in xml and "ibmi" in xml


def test_auto_keylock():
    xml = build_lpar_document(name="mypart", keylock="auto")
    assert "<KeylockPosition" in xml and "auto" in xml
