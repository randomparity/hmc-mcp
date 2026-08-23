"""XML request-document builders for the HMC REST API.

Each ``build_*_document`` produces the XML body for one HMC operation: LPAR /
VIOS / managed-system create and modify (LogicalPartition and ManagedSystem
documents), DLPAR processor/memory change documents, virtual adapters
(vSCSI / vFC / network), storage (volume groups, virtual disks, vSCSI
mappings), networking (virtual networks), virtual media (media repository,
optical media, brokered file upload and ISO import), and web resources
(session logon, HMC users, password policies, LDAP configuration).

The HMC creates an LPAR from a PUT of a LogicalPartition document to
/rest/api/uom/ManagedSystem/{uuid}/LogicalPartition, and modifies one with a
POST of a (partial) document to /rest/api/uom/LogicalPartition/{uuid}.

VIOS partitions use the same endpoint; partition_type="Virtual IO Server"
distinguishes them. build_vios_document is a thin wrapper that fixes that
type and exposes only the parameters relevant to VIOS provisioning.
A managed system is modified with a POST of a (partial) ManagedSystem document
to /rest/api/uom/ManagedSystem/{uuid}.

Only the fields we set are included; the HMC fills in everything else with
defaults (profiles, virtual adapters, boot mode, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, get_args

from .xmlutil import ATOM_NS, WEB_NS, escapes_string_arguments

UOM_NS = "http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/"

# Valid partition environments.
PARTITION_TYPES: tuple[PartitionType, ...] = ("AIX/Linux", "OS400", "Virtual IO Server")

# Closed vocabularies for the string/int attributes the document builders
# validate. The sets mirror what the HMC accepts for each property; os_type
# and keylock are LogicalPartition attributes, the rest are ManagedSystem
# modify attributes (vocabularies per IBM's ansible-power-hmc collection).
OS_TYPES = ("aix", "linux", "ibmi")
KEYLOCK_POSITIONS = ("normal", "manual", "auto")
# power_off_policy: 1 powers the managed system off after all partitions
# shut down, 0 leaves it powered on.
POWER_OFF_POLICIES = (0, 1)
POWER_ON_LPAR_START_POLICIES = ("autostart", "userinit", "autorecovery")
MEM_MIRRORING_MODES = ("none", "sys_firmware_only")

# Type aliases for the closed sets above, used to annotate the document-builder
# and MCP-tool parameters so MCP renders them as enums and static checkers catch
# typos before they reach the HMC.
PartitionType = Literal["AIX/Linux", "OS400", "Virtual IO Server"]
OsType = Literal["aix", "linux", "ibmi"]
Keylock = Literal["normal", "manual", "auto"]
PowerOffPolicy = Literal[0, 1]
PowerOnLparStartPolicy = Literal["autostart", "userinit", "autorecovery"]
MemoryMirroringMode = Literal["none", "sys_firmware_only"]
StorageKind = Literal["PhysicalVolume", "VirtualDisk"]
AuthenticationType = Literal["Local", "LDAP", "Kerberos"]
SharingMode = Literal[
    "capped",
    "uncapped",
    "keep_idle_procs",
    "share_idle_procs",
    "share_idle_procs_active",
    "share_idle_procs_always",
]

STORAGE_KINDS = frozenset(get_args(StorageKind))
AUTHENTICATION_TYPES = frozenset(get_args(AuthenticationType))
SHARING_MODES = frozenset(get_args(SharingMode))


# Boot device selectors for LPAR boot order configuration.
# These match IBM HMC boot device types and are used to construct
# PendingBootString values for boot order operations.
BOOT_DEVICE_SELECTORS: tuple[BootDeviceSelector, ...] = (
    "cd",  # Optical/CD-ROM device
    "disk",  # Disk device (SCSI, direct-attached, SAN)
    "network",  # Network boot (PXE, NIM, etc.)
)

# Type alias for boot device selectors used in PendingBootString construction.
BootDeviceSelector = Literal["cd", "disk", "network"]


@dataclass(frozen=True)
class LparResources:
    """Memory / processor resource fields shared by LPAR create, modify, DLPAR.

    This is the single source of truth for the resource vocabulary used by the
    LPAR document builders (``build_lpar_document``, ``build_dlpar_*``) and the
    MCP/CLI tool parameters that feed them. Memory values are in MiB. For
    shared partitions (``dedicated=False``) ``procs`` are processing units
    (may be fractional, e.g. 0.5) and ``vcpus`` are virtual processor counts;
    with ``dedicated=True`` the ``procs`` are whole CPU counts. A ``None``
    field means "not specified": the HMC supplies a default on create and
    leaves the value unchanged on modify / DLPAR. ``dedicated`` and ``uncapped``
    are also ``None``-able so a modify can change processor counts without
    silently flipping the sharing mode.
    """

    min_memory: int | None = field(
        default=None, metadata={"description": "Minimum memory in MiB."}
    )
    desired_memory: int | None = field(
        default=None, metadata={"description": "Desired memory in MiB."}
    )
    max_memory: int | None = field(
        default=None, metadata={"description": "Maximum memory in MiB."}
    )
    dedicated: bool | None = field(
        default=None,
        metadata={
            "description": "Whether processors are dedicated rather than shared."
        },
    )
    min_procs: float | None = field(
        default=None,
        metadata={
            "description": "Minimum whole CPUs when dedicated, or processing units when shared."
        },
    )
    desired_procs: float | None = field(
        default=None,
        metadata={
            "description": "Desired whole CPUs when dedicated, or processing units when shared."
        },
    )
    max_procs: float | None = field(
        default=None,
        metadata={
            "description": "Maximum whole CPUs when dedicated, or processing units when shared."
        },
    )
    min_vcpus: int | None = field(
        default=None,
        metadata={
            "description": "Minimum virtual processor count for a shared partition."
        },
    )
    desired_vcpus: int | None = field(
        default=None,
        metadata={
            "description": "Desired virtual processor count for a shared partition."
        },
    )
    max_vcpus: int | None = field(
        default=None,
        metadata={
            "description": "Maximum virtual processor count for a shared partition."
        },
    )
    sharing_mode: SharingMode | None = field(
        default=None, metadata={"description": "HMC processor sharing mode."}
    )
    uncapped: bool | None = field(
        default=None,
        metadata={
            "description": "Whether a shared partition may consume spare processing capacity."
        },
    )


def _memory_config(resources: LparResources) -> str:
    min_memory = resources.min_memory
    desired_memory = resources.desired_memory
    max_memory = resources.max_memory
    if min_memory is None and desired_memory is None and max_memory is None:
        return ""
    parts = [
        '  <PartitionMemoryConfiguration kb="CUD" kxe="false">',
        "    <Metadata><Atom/></Metadata>",
    ]
    if desired_memory is not None:
        parts.append(
            f'    <DesiredMemory kb="CUD" kxe="false">{desired_memory}</DesiredMemory>'
        )
    if max_memory is not None:
        parts.append(
            f'    <MaximumMemory kb="CUD" kxe="false">{max_memory}</MaximumMemory>'
        )
    if min_memory is not None:
        parts.append(
            f'    <MinimumMemory kb="CUD" kxe="false">{min_memory}</MinimumMemory>'
        )
    parts.append("  </PartitionMemoryConfiguration>")
    return "\n".join(parts)


def _validate_sharing_mode(sharing_mode: SharingMode | None) -> None:
    if sharing_mode is not None and (
        not isinstance(sharing_mode, str) or sharing_mode not in SHARING_MODES
    ):
        legal_values = ", ".join(sorted(SHARING_MODES))
        raise ValueError(f"sharing_mode must be one of: {legal_values}")


def _dedicated_processor_body(resources: LparResources) -> list[str]:
    parts = [
        '    <DedicatedProcessorConfiguration kb="CUD" kxe="false">',
        "      <Metadata><Atom/></Metadata>",
    ]
    fields = (
        ("DesiredProcessors", resources.desired_procs),
        ("MaximumProcessors", resources.max_procs),
        ("MinimumProcessors", resources.min_procs),
    )
    parts.extend(
        f'      <{name} kb="CUD" kxe="false">{int(value)}</{name}>'
        for name, value in fields
        if value is not None
    )
    parts.extend(
        (
            "    </DedicatedProcessorConfiguration>",
            '    <HasDedicatedProcessors kb="CUD" kxe="false">true</HasDedicatedProcessors>',
        )
    )
    if resources.sharing_mode:
        parts.append(
            f'    <SharingMode kb="CUD" kxe="false">{resources.sharing_mode}</SharingMode>'
        )
    return parts


def _shared_processor_body(resources: LparResources) -> list[str]:
    parts: list[str] = []
    if resources.dedicated is False:
        parts.append(
            '    <HasDedicatedProcessors kb="CUD" kxe="false">false</HasDedicatedProcessors>'
        )
    parts.extend(
        (
            '    <SharedProcessorConfiguration kb="CUD" kxe="false">',
            "      <Metadata><Atom/></Metadata>",
        )
    )
    fields = (
        ("DesiredProcessingUnits", resources.desired_procs),
        ("MaximumProcessingUnits", resources.max_procs),
        ("MinimumProcessingUnits", resources.min_procs),
        ("DesiredVirtualProcessors", resources.desired_vcpus),
        ("MaximumVirtualProcessors", resources.max_vcpus),
        ("MinimumVirtualProcessors", resources.min_vcpus),
    )
    for name, value in fields:
        if value is not None:
            rendered = (
                int(value) if isinstance(value, float) and value.is_integer() else value
            )
            parts.append(f'      <{name} kb="CUD" kxe="false">{rendered}</{name}>')
    if resources.uncapped is False:
        parts.append('      <UncappedWeight kb="CUD" kxe="false">0</UncappedWeight>')
    parts.append("    </SharedProcessorConfiguration>")
    sharing_mode = (
        "uncapped"
        if resources.uncapped is True
        else resources.sharing_mode
        or ("capped" if resources.uncapped is False else None)
    )
    if sharing_mode:
        parts.append(
            f'    <SharingMode kb="CUD" kxe="false">{sharing_mode}</SharingMode>'
        )
    return parts


def _processor_config(resources: LparResources) -> str:
    """Build PartitionProcessorConfiguration.

    dedicated=True  -> DedicatedProcessorConfiguration (whole CPUs).
    dedicated=False -> SharedProcessorConfiguration (processing units) plus
                       virtual processor min/desired/max.
    dedicated=None  -> the sharing mode is left unchanged: the processor
                       values are still emitted but HasDedicatedProcessors is
                       omitted so the HMC applies them to the current mode.
    For shared partitions, procs are processing units (may be fractional,
    e.g. 0.5); vcpus are the virtual processor counts (ints). uncapped=None
    likewise leaves SharingMode / UncappedWeight unchanged.
    """
    _validate_sharing_mode(resources.sharing_mode)
    have_procs = any(
        value is not None
        for value in (resources.min_procs, resources.desired_procs, resources.max_procs)
    )
    have_vcpus = any(
        value is not None
        for value in (resources.min_vcpus, resources.desired_vcpus, resources.max_vcpus)
    )
    if not (have_procs or have_vcpus):
        return ""

    parts = [
        '  <PartitionProcessorConfiguration kb="CUD" kxe="false">',
        "    <Metadata><Atom/></Metadata>",
    ]

    body_builder = (
        _dedicated_processor_body
        if resources.dedicated is True
        else _shared_processor_body
    )
    parts.extend(body_builder(resources))

    parts.append("  </PartitionProcessorConfiguration>")
    return "\n".join(parts)


def _document_envelope(root_element: str, body: str, namespace: str = UOM_NS) -> str:
    """Wrap a document body in the standard HMC XML envelope."""
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<{root_element} xmlns="{namespace}" schemaVersion="V1_0">
{body}
</{root_element}>
"""


def _lpar_envelope(body: str) -> str:
    """Wrap an LPAR document body in the LogicalPartition XML envelope."""
    return _document_envelope("LogicalPartition", body)


@escapes_string_arguments
def build_lpar_document(
    name: str | None,
    partition_type: PartitionType = "AIX/Linux",
    partition_id: int | None = None,
    resources: LparResources | None = None,
    os_type: OsType | None = None,
    keylock: Keylock | None = None,
    max_virtual_slots: int | None = None,
) -> str:
    """Build a LogicalPartition document for PUT (create) or POST (modify).

    For a create, `name` is required; the rest are optional (the HMC supplies
    defaults). For a modify, supply only the fields to change (pass name=None
    to omit it). `resources` carries the memory/processor fields; None means
    no resource block is emitted.

    os_type: target OS type — ``aix``, ``linux``, or ``ibmi``.
    keylock: initial keylock position — ``normal``, ``manual``, or ``auto``.
    max_virtual_slots: maximum number of virtual I/O slots.
    """
    if partition_type not in PARTITION_TYPES:
        raise ValueError(
            f"partition_type must be one of {PARTITION_TYPES}, got {partition_type!r}"
        )
    if os_type is not None and os_type not in OS_TYPES:
        raise ValueError(f"os_type must be one of {OS_TYPES}, got {os_type!r}")
    if keylock is not None and keylock not in KEYLOCK_POSITIONS:
        raise ValueError(f"keylock must be one of {KEYLOCK_POSITIONS}, got {keylock!r}")

    resources = resources or LparResources()

    body_parts = ["  <Metadata><Atom/></Metadata>"]
    if partition_id is not None:
        body_parts.append(
            f'  <PartitionID kb="COD" kxe="false">{partition_id}</PartitionID>'
        )

    mem = _memory_config(resources)
    if mem:
        body_parts.append(mem)

    if keylock is not None:
        body_parts.append(
            f'  <KeylockPosition kb="CUD" kxe="false">{keylock}</KeylockPosition>'
        )

    if max_virtual_slots is not None:
        body_parts.append(
            f'  <MaximumVirtualIoSlots kb="CUD" kxe="false">{max_virtual_slots}</MaximumVirtualIoSlots>'
        )

    if name is not None:
        body_parts.append(
            f'  <PartitionName kb="CUR" kxe="false">{name}</PartitionName>'
        )

    if os_type is not None:
        body_parts.append(
            f'  <OperatingSystemType kb="CUD" kxe="false">{os_type}</OperatingSystemType>'
        )

    proc = _processor_config(resources)
    if proc:
        body_parts.append(proc)

    body_parts.append(
        f'  <PartitionType kb="COD" kxe="false">{partition_type}</PartitionType>'
    )

    body = "\n".join(body_parts)
    return _lpar_envelope(body)


VIOS_DEFAULT_RESOURCES = LparResources(
    min_memory=512,
    desired_memory=4096,
    max_memory=8192,
    desired_vcpus=2,
    min_vcpus=1,
    max_vcpus=4,
    desired_procs=0.5,
    min_procs=0.1,
    max_procs=1.0,
    uncapped=True,
)


@escapes_string_arguments
def build_vios_document(
    name: str,
    resources: LparResources = VIOS_DEFAULT_RESOURCES,
) -> str:
    """Build a LogicalPartition document for creating a Virtual IO Server.

    Wraps build_lpar_document with partition_type='Virtual IO Server' and
    shared-processor defaults appropriate for VIOS provisioning.
    """
    return build_lpar_document(
        name=name,
        partition_type="Virtual IO Server",
        resources=resources,
    )


@escapes_string_arguments
def build_dlpar_proc_document(resources: LparResources | None = None) -> str:
    """Minimal LogicalPartition document containing only PartitionProcessorConfiguration.

    Used for DLPAR processor hot-plug: POST to /rest/api/uom/LogicalPartition/{uuid}.
    On a running partition this applies immediately if RMC is active; otherwise the
    change is profile-only and takes effect on next activation.

    For shared partitions, procs are processing units (may be fractional, e.g. 0.5);
    vcpus are the virtual processor counts (ints).
    Set dedicated=True to assign whole CPUs; dedicated=False (default) for shared.
    """
    resources = resources or LparResources()
    proc = _processor_config(resources)
    body = "  <Metadata><Atom/></Metadata>"
    if proc:
        body = body + "\n" + proc
    return _lpar_envelope(body)


@escapes_string_arguments
def build_dlpar_mem_document(resources: LparResources | None = None) -> str:
    """Minimal LogicalPartition document containing only PartitionMemoryConfiguration.

    Used for DLPAR memory hot-plug: POST to /rest/api/uom/LogicalPartition/{uuid}.
    On a running partition this applies immediately if RMC is active; otherwise the
    change is profile-only and takes effect on next activation.

    Memory values are in MiB. Only the min/desired/max memory fields of
    `resources` are emitted; processor fields are ignored.
    """
    resources = resources or LparResources()
    mem = _memory_config(resources)
    body = "  <Metadata><Atom/></Metadata>"
    if mem:
        body = body + "\n" + mem
    return _lpar_envelope(body)


@escapes_string_arguments
def build_managed_system_document(
    new_name: str | None = None,
    power_off_policy: PowerOffPolicy | None = None,
    power_on_lpar_start_policy: PowerOnLparStartPolicy | None = None,
    pend_mem_region_size: int | None = None,
    requested_num_sys_huge_pages: int | None = None,
    mem_mirroring_mode: MemoryMirroringMode | None = None,
) -> str:
    """Build a ManagedSystem document for POST (modify).

    All parameters are optional; omitted (None) fields are not included.
    The HMC merges the supplied fields with the existing system configuration.

    new_name: rename the managed system.
    power_off_policy: power-off policy — 1 powers the system off after all
        partitions shut down, 0 leaves it powered on.
    power_on_lpar_start_policy: LPAR auto-start policy on system power-on —
        'autostart', 'userinit', or 'autorecovery'.
    pend_mem_region_size: pending memory region size (MiB).
    requested_num_sys_huge_pages: number of huge memory pages to allocate.
    mem_mirroring_mode: memory mirroring mode — 'none' or 'sys_firmware_only'.
    """
    if power_off_policy is not None and power_off_policy not in POWER_OFF_POLICIES:
        raise ValueError(
            f"power_off_policy must be one of {POWER_OFF_POLICIES}, got "
            f"{power_off_policy!r}"
        )
    if (
        power_on_lpar_start_policy is not None
        and power_on_lpar_start_policy not in POWER_ON_LPAR_START_POLICIES
    ):
        raise ValueError(
            f"power_on_lpar_start_policy must be one of "
            f"{POWER_ON_LPAR_START_POLICIES}, got {power_on_lpar_start_policy!r}"
        )
    if mem_mirroring_mode is not None and mem_mirroring_mode not in MEM_MIRRORING_MODES:
        raise ValueError(
            f"mem_mirroring_mode must be one of {MEM_MIRRORING_MODES}, got "
            f"{mem_mirroring_mode!r}"
        )

    body_parts = ["  <Metadata><Atom/></Metadata>"]

    mem_fields = [
        mem_mirroring_mode,
        pend_mem_region_size,
        requested_num_sys_huge_pages,
    ]
    if any(v is not None for v in mem_fields):
        mem_parts = [
            '  <SystemMemoryConfiguration kb="CUD" kxe="false">',
            "    <Metadata><Atom/></Metadata>",
        ]
        if mem_mirroring_mode is not None:
            mem_parts.append(
                f'    <MemoryMirroringMode kb="CUD" kxe="false">{mem_mirroring_mode}</MemoryMirroringMode>'
            )
        if pend_mem_region_size is not None:
            mem_parts.append(
                f'    <PendingMemoryRegionSize kb="CUD" kxe="false">{pend_mem_region_size}</PendingMemoryRegionSize>'
            )
        if requested_num_sys_huge_pages is not None:
            mem_parts.append(
                f'    <RequestedHugeSystemMemoryPages kb="CUD" kxe="false">'
                f"{requested_num_sys_huge_pages}</RequestedHugeSystemMemoryPages>"
            )
        mem_parts.append("  </SystemMemoryConfiguration>")
        body_parts.append("\n".join(mem_parts))

    if new_name is not None:
        body_parts.append(f'  <SystemName kb="CUD" kxe="false">{new_name}</SystemName>')
    if power_off_policy is not None:
        body_parts.append(
            f'  <PowerOffPolicy kb="CUD" kxe="false">{power_off_policy}</PowerOffPolicy>'
        )
    if power_on_lpar_start_policy is not None:
        body_parts.append(
            f'  <PowerOnLparStartPolicy kb="CUD" kxe="false">{power_on_lpar_start_policy}</PowerOnLparStartPolicy>'
        )

    body = "\n".join(body_parts)
    return _document_envelope("ManagedSystem", body)


# ====================================================================== #
# Virtual adapters (children of LogicalPartition)
#
# Field names taken from IBM's HmcRestClient reference implementation and
# the HMC REST API spec:
#   VirtualSCSIClientAdapter          -> AdapterType, RemoteLogicalPartitionID,
#                                        RemoteSlotNumber, VirtualSlotNumber
#   VirtualFibreChannelClientAdapter  -> AdapterType, ConnectingPartitionID,
#                                        ConnectingVirtualSlotNumber, VirtualSlotNumber
#   ClientNetworkAdapter              -> PortVLANID, VirtualSlotNumber,
#                                        VirtualSwitchID, IsTaggedVLAN, MACAddress
# ====================================================================== #


def _adapter_document(
    root_element: str,
    partition_id_field: str,
    slot_field: str,
    vios_partition_id: int,
    vios_slot: int,
    slot_number: int | None = None,
) -> str:
    """Build a virtual client adapter document from the shared skeleton.

    *partition_id_field* / *slot_field* name the VIOS-side remote fields,
    which differ per adapter type (vSCSI vs vFC). The client VirtualSlotNumber
    is emitted only when *slot_number* is given; the HMC auto-assigns it
    otherwise.
    """
    slot = ""
    if slot_number is not None:
        slot = f'  <VirtualSlotNumber kb="CUD" kxe="false">{slot_number}</VirtualSlotNumber>\n'
    body = f"""  <Metadata><Atom/></Metadata>
  <AdapterType kb="CUD" kxe="false">Client</AdapterType>
{slot}  <{partition_id_field} kb="CUD" kxe="false">{vios_partition_id}</{partition_id_field}>
  <{slot_field} kb="CUD" kxe="false">{vios_slot}</{slot_field}>"""
    return _document_envelope(root_element, body)


@escapes_string_arguments
def build_vscsi_adapter_document(
    vios_partition_id: int,
    vios_slot: int,
    slot_number: int | None = None,
) -> str:
    """Virtual SCSI client adapter, paired to a VIOS server adapter.

    vios_partition_id / vios_slot identify the VIOS and its server-side slot
    that own the backing storage. slot_number is the client adapter's virtual
    slot (auto-assigned by the HMC if omitted).
    """
    return _adapter_document(
        "VirtualSCSIClientAdapter",
        "RemoteLogicalPartitionID",
        "RemoteSlotNumber",
        vios_partition_id,
        vios_slot,
        slot_number,
    )


@escapes_string_arguments
def build_vfc_adapter_document(
    vios_partition_id: int,
    vios_slot: int,
    slot_number: int | None = None,
) -> str:
    """Virtual Fibre Channel (NPIV) client adapter, paired to a VIOS.

    ConnectingPartitionID / ConnectingVirtualSlotNumber identify the VIOS and
    its server-side FC slot. The WWPNs are generated by the HMC on creation.
    """
    return _adapter_document(
        "VirtualFibreChannelClientAdapter",
        "ConnectingPartitionID",
        "ConnectingVirtualSlotNumber",
        vios_partition_id,
        vios_slot,
        slot_number,
    )


@escapes_string_arguments
def build_client_network_adapter_document(
    port_vlan_id: int,
    slot_number: int | None = None,
    virtual_switch_id: int | None = None,
    tagged: bool = False,
    mac_address: str | None = None,
) -> str:
    """Virtual Ethernet client network adapter.

    port_vlan_id is the PVID (the VLAN the adapter sits on). virtual_switch_id
    selects a specific vSwitch (default switch used if omitted). tagged=True
    makes this a tagged (VLAN-trunking) adapter; mac_address pins the MAC
    (otherwise the HMC generates one).
    """
    parts = ["  <Metadata><Atom/></Metadata>"]
    if slot_number is not None:
        parts.append(
            f'  <VirtualSlotNumber kb="CUD" kxe="false">{slot_number}</VirtualSlotNumber>'
        )
    if virtual_switch_id is not None:
        parts.append(
            f'  <VirtualSwitchID kb="CUD" kxe="false">{virtual_switch_id}</VirtualSwitchID>'
        )
    parts.append(f'  <PortVLANID kb="CUD" kxe="false">{port_vlan_id}</PortVLANID>')
    if tagged:
        parts.append('  <IsTaggedVLAN kb="CUD" kxe="false">true</IsTaggedVLAN>')
    if mac_address:
        parts.append(f'  <MACAddress kb="CUD" kxe="false">{mac_address}</MACAddress>')
    body = "\n".join(parts)
    return _document_envelope("ClientNetworkAdapter", body)


# ====================================================================== #
# Virtual storage (children of VirtualIOServer)
#
# Model (from IBM's HmcRestClient reference + the HMC REST spec):
#   VolumeGroup      PUT to VirtualIOServer/{uuid}/VolumeGroup creates;
#                    POST to .../VolumeGroup/{vg_uuid} modifies (add PVs,
#                    create/delete/extend Virtual Disks, media repository).
#   VirtualDisk      a logical volume carved from a VG; created via a POST of
#                    the VolumeGroup document carrying a VirtualDisks block.
#   VirtualSCSIMapping  POST to the VIOS document carrying a
#                    VirtualSCSIMappings block; connects a backing storage
#                    (PhysicalVolume or VirtualDisk) to an LPAR (Atom link).
# ====================================================================== #


@escapes_string_arguments
def build_volume_group_document(name: str, physical_volumes: list[str]) -> str:
    """Document to create a Volume Group from a set of physical volumes."""
    pvs = "\n".join(
        f'    <PhysicalVolume kb="CUD" kxe="false" schemaVersion="V1_0">\n'
        f"      <Metadata><Atom/></Metadata>\n"
        f'      <VolumeName kb="CUD" kxe="false">{pv}</VolumeName>\n'
        f"    </PhysicalVolume>"
        for pv in physical_volumes
    )
    body = f"""  <Metadata><Atom/></Metadata>
  <GroupName kb="CUD" kxe="false">{name}</GroupName>
  <PhysicalVolumes kb="CUD" kxe="false" schemaVersion="V1_0">
    <Metadata><Atom/></Metadata>
{pvs}
  </PhysicalVolumes>"""
    return _document_envelope("VolumeGroup", body)


@escapes_string_arguments
def build_virtual_disk_document(disk_name: str, capacity_mib: int) -> str:
    """A VolumeGroup document carrying a new VirtualDisk (for create POST)."""
    body = f"""  <Metadata><Atom/></Metadata>
  <VirtualDisks kb="CUD" kxe="false" schemaVersion="V1_0">
    <Metadata><Atom/></Metadata>
    <VirtualDisk kb="CUD" kxe="false" schemaVersion="V1_0">
      <Metadata><Atom/></Metadata>
      <DiskName kb="CUD" kxe="false">{disk_name}</DiskName>
      <DiskCapacity kb="CUD" kxe="false">{capacity_mib}</DiskCapacity>
    </VirtualDisk>
  </VirtualDisks>"""
    return _document_envelope("VolumeGroup", body)


@escapes_string_arguments
def build_vscsi_mapping_document(
    storage_kind: StorageKind,
    storage_name: str,
    lpar_link: str,
    target_device: str | None = None,
) -> str:
    """A VirtualIOServer document carrying a VirtualSCSIMapping (for POST).

    storage_kind is "PhysicalVolume" (whole disk) or "VirtualDisk" (a logical
    volume from a VG). storage_name is the device/disk name (e.g. hdisk5 or
    the DiskName). lpar_link is the Atom SELF href of the client LPAR the
    storage is mapped to. target_device optionally pins the vtscsi name.
    """
    # storage_kind is the one caller value that becomes an element *name*
    # below, and escaping cannot make a name safe. This check, not the
    # escaping decorator, is what protects that site; it still fires because
    # escaping is the identity on the two legal values.
    if storage_kind not in STORAGE_KINDS:
        raise ValueError(
            f"storage_kind must be PhysicalVolume or VirtualDisk, got {storage_kind!r}"
        )
    name_field = "VolumeName" if storage_kind == "PhysicalVolume" else "DiskName"
    target = ""
    if target_device:
        target = (
            f'      <TargetDevice kb="CUD" kxe="false">{target_device}</TargetDevice>\n'
        )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<VirtualIOServer xmlns="{UOM_NS}" xmlns:atom="{ATOM_NS}" schemaVersion="V1_0">
  <Metadata><Atom/></Metadata>
  <VirtualSCSIMappings kb="CUD" kxe="false" schemaVersion="V1_0">
    <Metadata><Atom/></Metadata>
    <VirtualSCSIMapping kb="CUD" kxe="false" schemaVersion="V1_0">
      <Metadata><Atom/></Metadata>
      <Storage kb="CUD" kxe="false" schemaVersion="V1_0">
        <Metadata><Atom/></Metadata>
        <{storage_kind} kb="CUD" kxe="false" schemaVersion="V1_0">
          <Metadata><Atom/></Metadata>
          <{name_field} kb="CUD" kxe="false">{storage_name}</{name_field}>
        </{storage_kind}>
      </Storage>
{target}      <AssociatedLogicalPartition xmlns="{ATOM_NS}" rel="related" href="{lpar_link}"/>
    </VirtualSCSIMapping>
  </VirtualSCSIMappings>
</VirtualIOServer>
"""


@escapes_string_arguments
def build_virtual_optical_mapping_document(
    media_name: str,
    lpar_link: str,
    target_device: str | None = None,
) -> str:
    """A VirtualIOServer document carrying a VirtualSCSIMapping for optical media (for POST).

    media_name is the MediaName of the VirtualOpticalMedia (ISO container) to mount.
    lpar_link is the Atom SELF href of the client LPAR the optical media is mapped to.
    target_device optionally pins the vtscsi name. This creates a read-only optical mapping.
    """
    target = ""
    if target_device:
        target = (
            f'      <TargetDevice kb="CUD" kxe="false">{target_device}</TargetDevice>\n'
        )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<VirtualIOServer xmlns="{UOM_NS}" xmlns:atom="{ATOM_NS}" schemaVersion="V1_0">
  <Metadata><Atom/></Metadata>
  <VirtualSCSIMappings kb="CUD" kxe="false" schemaVersion="V1_0">
    <Metadata><Atom/></Metadata>
    <VirtualSCSIMapping kb="CUD" kxe="false" schemaVersion="V1_0">
      <Metadata><Atom/></Metadata>
      <Storage kb="CUD" kxe="false" schemaVersion="V1_0">
        <Metadata><Atom/></Metadata>
        <VirtualOpticalMedia kb="CUD" kxe="false" schemaVersion="V1_0">
          <Metadata><Atom/></Metadata>
          <MediaName kb="CUD" kxe="false">{media_name}</MediaName>
        </VirtualOpticalMedia>
      </Storage>
{target}      <AssociatedLogicalPartition xmlns="{ATOM_NS}" rel="related" href="{lpar_link}"/>
    </VirtualSCSIMapping>
  </VirtualSCSIMappings>
</VirtualIOServer>
"""


# ====================================================================== #
# Virtual Network (child of ManagedSystem)
#
# Create: PUT /rest/api/uom/ManagedSystem/{sys}/VirtualNetwork
# Fields: NetworkName, NetworkVLANID, VswitchID, TaggedNetwork, and an
# AssociatedSwitch Atom link to the backing VirtualSwitch.
# ====================================================================== #


@escapes_string_arguments
def build_virtual_network_document(
    name: str,
    vlan_id: int,
    virtual_switch_id: int,
    switch_link: str | None = None,
    tagged: bool = False,
) -> str:
    """Document to create a Virtual Network (VLAN) on a managed system.

    switch_link is the Atom href of the backing VirtualSwitch
    (.../ManagedSystem/{sys}/VirtualSwitch/{uuid}); when given it is emitted as
    the AssociatedSwitch link. tagged controls TaggedNetwork.
    """
    assoc = ""
    if switch_link:
        assoc = (
            f'  <AssociatedSwitch xmlns="{ATOM_NS}" rel="related" '
            f'href="{switch_link}"/>\n'
        )
    tagged_str = "true" if tagged else "false"
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<VirtualNetwork xmlns="{UOM_NS}" xmlns:atom="{ATOM_NS}" schemaVersion="V1_0">
  <Metadata><Atom/></Metadata>
{assoc}  <NetworkName kb="CUD" kxe="false">{name}</NetworkName>
  <NetworkVLANID kb="CUD" kxe="false">{vlan_id}</NetworkVLANID>
  <VswitchID kb="CUD" kxe="false">{virtual_switch_id}</VswitchID>
  <TaggedNetwork kb="CUD" kxe="false">{tagged_str}</TaggedNetwork>
</VirtualNetwork>
"""


# ====================================================================== #
# Virtual Media Repository / Virtual Optical Media
#
# Both are operations via POST on a VolumeGroup (the repository lives on the
# "VMLibrary" volume group of a VIOS). The repository name is always
# "VMLibrary"; only BLANK optical media can be created via this API.
# ====================================================================== #


@escapes_string_arguments
def build_media_repository_document(size_mib: int, vg_name: str = "") -> str:
    """VolumeGroup document carrying a VirtualMediaRepository (create POST).

    The repository is always named VMLibrary; size_mib is RepositorySize.
    vg_name is the GroupName of the target VolumeGroup (required by HMC V10R3+).
    VirtualMediaRepository must be wrapped in MediaRepositories per the HMC schema.
    """
    group_name_element = f"\n  <GroupName>{vg_name}</GroupName>" if vg_name else ""
    body = f"""  <Metadata><Atom/></Metadata>{group_name_element}
  <MediaRepositories schemaVersion="V1_0">
    <Metadata><Atom/></Metadata>
    <VirtualMediaRepository schemaVersion="V1_0">
      <Metadata><Atom/></Metadata>
      <RepositoryName>VMLibrary</RepositoryName>
      <RepositorySize>{size_mib}</RepositorySize>
    </VirtualMediaRepository>
  </MediaRepositories>"""
    return _document_envelope("VolumeGroup", body)


@escapes_string_arguments
def build_virtual_optical_media_document(
    media_name: str, size_mib: int, vg_name: str = ""
) -> str:
    """VolumeGroup document carrying a blank VirtualOpticalMedia (create POST).

    Only blank optical media can be created via the API; media_name is the
    file name (e.g. 'aix.iso'), size_mib is MediaSize.
    vg_name is the GroupName of the target VolumeGroup (required by HMC V10R3+).
    VirtualMediaRepository must be wrapped in MediaRepositories per the HMC schema.
    """
    group_name_element = f"\n  <GroupName>{vg_name}</GroupName>" if vg_name else ""
    body = f"""  <Metadata><Atom/></Metadata>{group_name_element}
  <MediaRepositories schemaVersion="V1_0">
    <Metadata><Atom/></Metadata>
    <VirtualMediaRepository schemaVersion="V1_0">
      <Metadata><Atom/></Metadata>
      <VirtualOpticalMedia schemaVersion="V1_0">
        <Metadata><Atom/></Metadata>
        <MediaName>{media_name}</MediaName>
        <MediaSize>{size_mib}</MediaSize>
        <MediaType>BLANK</MediaType>
      </VirtualOpticalMedia>
    </VirtualMediaRepository>
  </MediaRepositories>"""
    return _document_envelope("VolumeGroup", body)


@escapes_string_arguments
def build_media_repository_delete_document(vg_name: str = "") -> str:
    """VolumeGroup document marking the VirtualMediaRepository for deletion (POST).

    vg_name is the GroupName of the target VolumeGroup (required by HMC V10R3+).
    VirtualMediaRepository must be wrapped in MediaRepositories per the HMC schema.
    """
    group_name_element = f"\n  <GroupName>{vg_name}</GroupName>" if vg_name else ""
    body = f"""  <Metadata><Atom/></Metadata>{group_name_element}
  <MediaRepositories schemaVersion="V1_0">
    <Metadata><Atom/></Metadata>
    <VirtualMediaRepository schemaVersion="V1_0">
      <Metadata><Atom/></Metadata>
      <RepositoryName>VMLibrary</RepositoryName>
    </VirtualMediaRepository>
  </MediaRepositories>"""
    return _document_envelope("VolumeGroup", body)


@escapes_string_arguments
def build_virtual_optical_media_delete_document(
    media_name: str, vg_name: str = ""
) -> str:
    """VolumeGroup document marking a VirtualOpticalMedia for deletion (POST).

    vg_name is the GroupName of the target VolumeGroup (required by HMC V10R3+).
    VirtualMediaRepository must be wrapped in MediaRepositories per the HMC schema.
    """
    group_name_element = f"\n  <GroupName>{vg_name}</GroupName>" if vg_name else ""
    body = f"""  <Metadata><Atom/></Metadata>{group_name_element}
  <MediaRepositories schemaVersion="V1_0">
    <Metadata><Atom/></Metadata>
    <VirtualMediaRepository schemaVersion="V1_0">
      <Metadata><Atom/></Metadata>
      <VirtualOpticalMedia schemaVersion="V1_0">
        <Metadata><Atom/></Metadata>
        <MediaName>{media_name}</MediaName>
      </VirtualOpticalMedia>
    </VirtualMediaRepository>
  </MediaRepositories>"""
    return _document_envelope("VolumeGroup", body)


@escapes_string_arguments
def build_virtual_disk_delete_document(disk_name: str) -> str:
    """VolumeGroup document marking a VirtualDisk for deletion (POST)."""
    body = f"""  <Metadata><Atom/></Metadata>
  <VirtualDisks schemaVersion="V1_0" kb="CUD">
    <Metadata><Atom/></Metadata>
    <VirtualDisk kb="CUD">
      <Metadata><Atom/></Metadata>
      <VolumeGroupName kb="CUD" kxe="false">{disk_name}</VolumeGroupName>
    </VirtualDisk>
  </VirtualDisks>"""
    return _document_envelope("VolumeGroup", body)


# ====================================================================== #
# Brokered file upload / ISO import (ADR 0031)
#
# Create:  POST /rest/api/uom/VirtualIOServer/{uuid}/VolumeGroup/{uuid}
#          with a BrokeredFile document; the broker URI comes back in the
#          Location header.
# Import:  POST to the same path with a LinkedVirtualOpticalMedia document
#          naming that broker URI.
#
# Neither document carries schemaVersion, so they render their own envelope
# rather than going through _document_envelope. Both are transport
# primitives for #203's future public API and are not exposed today.
# ====================================================================== #


@escapes_string_arguments
def build_brokered_file_document(filename: str) -> str:
    """BrokeredFile document creating an upload handle (create POST).

    ADR 0031 derived this shape from IBM's REST API documentation and the
    existing uom patterns rather than from a live HMC, so the exact structure
    is version-dependent and still unverified against hardware.
    """
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<BrokeredFile xmlns="{UOM_NS}">
  <Filename>{filename}</Filename>
</BrokeredFile>
"""


@escapes_string_arguments
def build_linked_optical_media_document(media_name: str, broker_uri: str) -> str:
    """LinkedVirtualOpticalMedia document importing an uploaded file (POST).

    ``broker_uri`` is the Location header the HMC returned from the brokered
    file create. It is escaped like any other value: escaping is the identity
    for a URI free of the five metacharacters, and an HMC that ever returned
    one carrying them would otherwise break the document.
    """
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<LinkedVirtualOpticalMedia xmlns="{UOM_NS}">
  <MediaName>{media_name}</MediaName>
  <LinkedFileURI>{broker_uri}</LinkedFileURI>
</LinkedVirtualOpticalMedia>
"""


# ====================================================================== #
# Session logon (/rest/api/web/Logon)
#
# Authenticate: PUT /rest/api/web/Logon with a LogonRequest document; the
# response carries the X-API-Session token.
# ====================================================================== #


@escapes_string_arguments
def build_logon_request_document(user: str, password: str) -> str:
    """LogonRequest document carrying the configured HMC credentials (PUT).

    The credentials arrive from ``HMCConfig`` rather than from a tool
    argument, which is why they reach this boundary as an explicit builder
    call instead of through a decorator on the client method: an
    argument-boundary decorator on ``HMCClient.logon`` would never see them.
    """
    body = f"""  <Metadata>
    <Atom/>
  </Metadata>
  <UserID kb="CUR" kxe="false">{user}</UserID>
  <Password kb="CUR" kxe="false">{password}</Password>"""
    return _document_envelope("LogonRequest", body, WEB_NS)


# ====================================================================== #
# UOM UserProfile and ManagementConsole RemoteAccess documents
# ====================================================================== #


@escapes_string_arguments
def build_hmc_user_document(
    user_id: str | None = None,
    authentication_type: AuthenticationType | None = None,
    password: str | None = None,
    description: str | None = None,
    associated_task_role: str | None = None,
    associated_resource_roles: list[str] | None = None,
    password_expiry: int | None = None,
    session_timeout: int | None = None,
    verify_session_timeout: bool | None = None,
    idle_session_timeout: int | None = None,
    user_inactivity: int | None = None,
    minimum_password_age: int | None = None,
    allow_web_remote_access: bool | None = None,
    allow_ssh_remote_access: bool | None = None,
    remote_user_id: str | None = None,
) -> str:
    """Build a documented UOM ``UserProfile`` create or update document."""
    parts = ["  <Metadata><Atom/></Metadata>"]
    if user_id is not None:
        parts.append(f'  <UserID kb="CUR" kxe="false">{user_id}</UserID>')
    if authentication_type is not None:
        if authentication_type not in AUTHENTICATION_TYPES:
            raise ValueError(
                f"Invalid authentication_type {authentication_type!r}. Must be one of: "
                f"{', '.join(sorted(AUTHENTICATION_TYPES))}"
            )
        parts.append(
            f'  <AuthenticationType kb="CUR" kxe="false">'
            f"{authentication_type}</AuthenticationType>"
        )
    if password is not None:
        parts.append(
            f'  <UserProfilePassword kb="CUR" kxe="false">'
            f"{password}</UserProfilePassword>"
        )
    if description is not None:
        parts.append(
            f'  <UserDescription kb="CUR" kxe="false">{description}</UserDescription>'
        )
    if associated_task_role is not None:
        parts.append(f'  <AssociatedTaskRole href="{associated_task_role}"/>')
    if associated_resource_roles:
        parts.append("  <AssociatedResourceRoles>")
        parts.extend(
            f'    <ResourceRole href="{role}"/>' for role in associated_resource_roles
        )
        parts.append("  </AssociatedResourceRoles>")
    for name, value in (
        ("PasswordExpiry", password_expiry),
        ("SessionTimeout", session_timeout),
        ("VerifySessionTimeout", verify_session_timeout),
        ("IdleSessionTimeout", idle_session_timeout),
        ("UserInactivity", user_inactivity),
        ("MinimumPasswordAge", minimum_password_age),
        ("AllowWebRemoteAccess", allow_web_remote_access),
        ("AllowSSHRemoteAccess", allow_ssh_remote_access),
        ("RemoteUserID", remote_user_id),
    ):
        if value is not None:
            rendered = str(value).lower() if isinstance(value, bool) else value
            parts.append(f'  <{name} kb="CUR" kxe="false">{rendered}</{name}>')
    return _document_envelope("UserProfile", "\n".join(parts), UOM_NS)


REMOTE_ACCESS_FIELDS = frozenset(
    {
        "LdapEnabled",
        "PrimaryLdapUri",
        "SecondaryLdapUri",
        "TLSEncryptionEnabled",
        "UseNonAnonymousBinding",
        "BindDistinguishedName",
        "BindPassword",
        "LoginAttribute",
        "BaseDistinguishedName",
        "SearchScope",
        "AutoManageEnabled",
        "UserPolicyAtrribute",
        "SearchFilter",
        "LdapGroupLogin",
        "LdapGroupMemberAttribute",
        "KerberosAuthenticationEnabled",
        "kerberosRemoteUserId",
        "KerberosEnabled",
        "DefaultRealm",
        "ClockSkew",
        "TicketLifeTime",
        "AuthenticationTimeOut",
        "RealmConfig",
        "KerberosRealm",
        "Hostname",
        "Realm",
    }
)


@escapes_string_arguments
def build_remote_access_document(
    values: dict[str, str | int | bool] | None = None,
    clear_fields: list[str] | None = None,
) -> str:
    """Build a partial documented ``ManagementConsole`` RemoteAccess document."""
    supplied = values or {}
    cleared = clear_fields or []
    unknown = (set(supplied) | set(cleared)) - REMOTE_ACCESS_FIELDS
    if unknown:
        raise ValueError(f"Unknown RemoteAccess fields: {', '.join(sorted(unknown))}")
    conflicts = set(supplied) & set(cleared)
    if conflicts:
        raise ValueError(
            f"RemoteAccess fields both set and cleared: {', '.join(sorted(conflicts))}"
        )
    if not supplied and not cleared:
        raise ValueError("RemoteAccess update must set or clear at least one field")
    parts = ["  <Metadata><Atom/></Metadata>"]
    for name, value in supplied.items():
        rendered = str(value).lower() if isinstance(value, bool) else value
        parts.append(f'  <{name} kb="CUR" kxe="false">{rendered}</{name}>')
    parts.extend(f'  <{name} kb="CUR" kxe="false"/>' for name in cleared)
    return _document_envelope("ManagementConsole", "\n".join(parts), UOM_NS)


# ====================================================================== #
# LPAR Boot Order (PendingBootString / BootListInformation)
#
# PendingBootString controls the boot device order for an LPAR's next boot.
# It's a space-separated list of boot device selectors (cd, disk, network)
# that determines the priority order. The HMC stores this in the
# BootListInformation element of a LogicalPartition.
#
# Operations:
# - build_boot_order_document: Set a custom boot order
# - build_clear_boot_order_document: Clear the boot order (restore defaults)
# ====================================================================== #


def _build_pending_boot_string(devices: list[str]) -> str:
    """Build a PendingBootString from validated boot device selectors.

    Args:
        devices: Ordered list of boot device selectors (cd, disk, network). Validated against BOOT_DEVICE_SELECTORS.

    Returns:
        Space-separated string of device selectors.
    """
    if not devices:
        raise ValueError("Boot order must contain at least one device")

    # Validate all selectors
    for device in devices:
        if device not in BOOT_DEVICE_SELECTORS:
            raise ValueError(
                f"Invalid boot device selector: {device!r}. "
                f"Must be one of: {BOOT_DEVICE_SELECTORS}"
            )

    return " ".join(devices)


@escapes_string_arguments
def build_boot_order_document(devices: list[str]) -> str:
    """Build a LogicalPartition document to set LPAR boot order.

    This document sets the PendingBootString which controls the boot device
    priority for the next LPAR boot. Changes take effect on the next activation
    (no reboot is required - this is a profile-only change).

    Args:
        devices: Ordered list of boot device selectors (cd, disk, network). Validated against BOOT_DEVICE_SELECTORS.
                 The first device is tried first, then the second, etc.

    Returns:
        XML document for POST to /rest/api/uom/LogicalPartition/{uuid}.

    Example:
        >>> xml = build_boot_order_document(["network", "cd", "disk"])
        >>> "PendingBootString" in xml
        True
    """
    pending_boot_string = _build_pending_boot_string(devices)

    body = f"""  <PendingBootString kb="CUR" kxe="false">{pending_boot_string}</PendingBootString>"""

    return _lpar_envelope(body)


@escapes_string_arguments
def build_clear_boot_order_document() -> str:
    """Build a LogicalPartition document to clear LPAR boot order.

    This document clears the PendingBootString, restoring the HMC default
    boot behavior. Changes take effect on the next activation.

    Returns:
        XML document for POST to /rest/api/uom/LogicalPartition/{uuid}.
    """
    body = """  <PendingBootString kb="CUR" kxe="false"></PendingBootString>"""

    return _lpar_envelope(body)
