from __future__ import annotations

from ..documents_shared import document_envelope
from ..xmlutil import escapes_string_arguments
from .common import (
    MEM_MIRRORING_MODES,
    POWER_OFF_POLICIES,
    POWER_ON_LPAR_START_POLICIES,
    MemoryMirroringMode,
    PowerOffPolicy,
    PowerOnLparStartPolicy,
)


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
    return document_envelope("ManagedSystem", body)


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
