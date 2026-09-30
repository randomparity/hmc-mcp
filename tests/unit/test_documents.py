"""Tests for the LogicalPartition create builder and the modify field mapping."""

import xml.etree.ElementTree as ET

import pytest

from hmcpctl import documents
from hmcpctl.documents import (
    AUTHENTICATION_TYPES,
    PARTITION_TYPES,
    SHARING_MODES,
    LparResources,
    build_hmc_user_document,
    build_lpar_document,
    partition_updates,
)
from hmcpctl.documents.common import UOM_NS

_EMPTY_LPAR = ET.fromstring(
    '<LogicalPartition xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/"/>'
)


@pytest.mark.parametrize(
    ("name", "owner"),
    [
        ("AUTHENTICATION_TYPES", "access"),
        ("AuthenticationType", "access"),
        ("join_boot_device_paths", "boot"),
        ("KEYLOCK_POSITIONS", "lpar"),
        ("OS_TYPES", "lpar"),
        ("PARTITION_TYPES", "lpar"),
        ("SHARING_MODES", "lpar"),
        ("Keylock", "lpar"),
        ("LparResources", "lpar"),
        ("OsType", "lpar"),
        ("PartitionType", "lpar"),
        ("SharingMode", "lpar"),
        ("STORAGE_KINDS", "storage"),
        ("StorageKind", "storage"),
        ("MEM_MIRRORING_MODES", "system"),
        ("POWER_OFF_POLICIES", "system"),
        ("POWER_ON_LPAR_START_POLICIES", "system"),
        ("MemoryMirroringMode", "system"),
        ("PowerOffPolicy", "system"),
        ("PowerOnLparStartPolicy", "system"),
    ],
)
def test_document_facade_exports_domain_owned_objects(name, owner):
    assert getattr(documents, name) is getattr(getattr(documents, owner), name)


@pytest.mark.parametrize(
    "name",
    [
        "AUTHENTICATION_TYPES",
        "AuthenticationType",
        "ATOM_NS",
        "DET",
        "ET",
        "KEYLOCK_POSITIONS",
        "LparResources",
        "MEM_MIRRORING_MODES",
        "OS_TYPES",
        "PARTITION_TYPES",
        "POWER_OFF_POLICIES",
        "POWER_ON_LPAR_START_POLICIES",
        "SHARING_MODES",
        "STORAGE_KINDS",
        "StorageKind",
        "WEB_NS",
        "_dedicated_processor_body",
        "_memory_config",
        "_processor_config",
        "_shared_processor_body",
        "_validate_sharing_mode",
        "escapes_string_arguments",
        "lpar_envelope",
    ],
)
def test_common_has_no_domain_or_xml_primitive_exports(name):
    assert not hasattr(documents.common, name)


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
        resources=LparResources(min_memory=256, desired_memory=512, max_memory=1024),
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


def test_shared_processor_config_states_the_mode():
    """V10R3 refuses a shared create without the mode: REST0126 proc_mode (#1161 P38)."""
    xml = build_lpar_document(
        name="m",
        resources=LparResources(desired_procs=0.5, desired_vcpus=1),
    )
    assert "SharedProcessorConfiguration" in xml
    assert "DesiredProcessingUnits" in xml and "0.5" in xml
    assert ">false</HasDedicatedProcessors>" in xml
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


@pytest.mark.parametrize(
    ("dedicated", "desired_procs"), [(True, 1), (False, 1), (None, None)]
)
def test_invalid_sharing_mode_is_rejected_before_xml(dedicated, desired_procs):
    illegal = "uncapped</SharingMode><Injected>true</Injected>"

    with pytest.raises(ValueError) as exc_info:
        build_lpar_document(
            name="bad",
            resources=LparResources(
                dedicated=dedicated,
                desired_procs=desired_procs,
                sharing_mode=illegal,
            ),
        )

    message = str(exc_info.value)
    assert "sharing_mode" in message
    assert all(value in message for value in SHARING_MODES)
    assert illegal not in message


@pytest.mark.parametrize("malformed", [["uncapped"], {"mode": "uncapped"}])
@pytest.mark.parametrize(
    "builder",
    [
        lambda resources: build_lpar_document(name="bad", resources=resources),
        lambda resources: partition_updates(_EMPTY_LPAR, resources=resources),
    ],
)
def test_malformed_sharing_mode_type_raises_actionable_value_error(malformed, builder):
    with pytest.raises(ValueError) as exc_info:
        builder(LparResources(desired_procs=1, sharing_mode=malformed))

    message = str(exc_info.value)
    assert "sharing_mode" in message
    assert all(value in message for value in SHARING_MODES)


# LogicalPartitionProcessorSharingMode.Enum in the live V10R3 schema (#1161 P31);
# "proces" is the HMC's own spelling.
REST_SHARING_MODES = {
    "capped": "capped",
    "uncapped": "uncapped",
    "keep_idle_procs": "keep idle procs",
    "share_idle_procs": "sre idle proces",
    "share_idle_procs_active": "sre idle procs active",
    "share_idle_procs_always": "sre idle procs always",
}


@pytest.mark.parametrize("sharing_mode", SHARING_MODES)
def test_all_sharing_modes_serialize_in_rest_spelling(sharing_mode):
    dedicated = sharing_mode not in {"capped", "uncapped"}
    xml = build_lpar_document(
        name="ok",
        resources=LparResources(
            dedicated=dedicated, desired_procs=1, sharing_mode=sharing_mode
        ),
    )

    assert f">{REST_SHARING_MODES[sharing_mode]}</SharingMode>" in xml


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


def test_all_authentication_types_serialize_unchanged():
    for authentication_type in AUTHENTICATION_TYPES:
        xml = build_hmc_user_document(
            user_id="operator", authentication_type=authentication_type
        )
        assert f">{authentication_type}</AuthenticationType>" in xml


def test_invalid_authentication_type_is_rejected():
    with pytest.raises(ValueError, match="authentication_type"):
        build_hmc_user_document(user_id="operator", authentication_type="radius")


def test_os_type_is_not_sent():
    """OperatingSystemType is read-only and needs a ksv; the HMC sets AIX/Linux (#1161 P39)."""
    xml = build_lpar_document(name="mypart", os_type="aix")
    assert "OperatingSystemType" not in xml


def test_keylock_emitted():
    xml = build_lpar_document(name="mypart", keylock="normal")
    assert "<KeylockPosition" in xml
    assert "normal" in xml


def test_max_virtual_slots_emitted():
    """The slots live in PartitionIOConfiguration, which V10R3 honors (#1161 P39)."""
    root = ET.fromstring(
        build_lpar_document(name="mypart", max_virtual_slots=64).encode("utf-8")
    )
    io = root.find(f"{{{UOM_NS}}}PartitionIOConfiguration")
    assert io is not None and io.get("schemaVersion") == "V1_0"
    assert io.findtext(f"{{{UOM_NS}}}MaximumVirtualIOSlots") == "64"


def test_all_three_new_fields_together():
    xml = build_lpar_document(
        name="mypart",
        os_type="linux",
        keylock="manual",
        max_virtual_slots=32,
    )
    assert "OperatingSystemType" not in xml
    assert "<KeylockPosition" in xml and "manual" in xml
    assert "<MaximumVirtualIOSlots" in xml and "32" in xml


def test_new_fields_default_to_none_backward_compat():
    xml = build_lpar_document(name="mypart")
    assert "OperatingSystemType" not in xml
    assert "KeylockPosition" not in xml
    assert "PartitionIOConfiguration" not in xml


@pytest.mark.parametrize("keylock", ["auto", "unknown", "norm"])
def test_keylock_outside_the_schema_is_refused(keylock):
    """V10R3's KeylockPosition.Enum is manual, normal, unknown; unknown is not creatable."""
    with pytest.raises(ValueError, match="keylock must be one of: normal, manual"):
        build_lpar_document(name="mypart", keylock=keylock)
