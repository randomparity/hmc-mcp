"""XML builders for LogicalPartition create/modify documents.

The HMC creates an LPAR from a PUT of a LogicalPartition document to
/rest/api/uom/ManagedSystem/{uuid}/LogicalPartition, and modifies one with a
POST of a (partial) document to /rest/api/uom/LogicalPartition/{uuid}.

VIOS partitions use the same endpoint; partition_type="Virtual IO Server"
distinguishes them. build_vios_document is a thin wrapper that fixes that
type and exposes only the parameters relevant to VIOS provisioning.

Only the fields we set are included; the HMC fills in everything else with
defaults (profiles, virtual adapters, boot mode, etc.).
"""

from __future__ import annotations

from .xmlutil import ATOM_NS

UOM_NS = "http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/"

# Valid partition environments.
PARTITION_TYPES = ("AIX/Linux", "OS400", "Virtual IO Server")


def _memory_config(
    min_memory: int | None, desired_memory: int | None, max_memory: int | None
) -> str:
    if min_memory is None and desired_memory is None and max_memory is None:
        return ""
    parts = ["  <PartitionMemoryConfiguration kb=\"CUD\" kxe=\"false\">", "    <Metadata><Atom/></Metadata>"]
    if desired_memory is not None:
        parts.append(f'    <DesiredMemory kb="CUD" kxe="false">{desired_memory}</DesiredMemory>')
    if max_memory is not None:
        parts.append(f'    <MaximumMemory kb="CUD" kxe="false">{max_memory}</MaximumMemory>')
    if min_memory is not None:
        parts.append(f'    <MinimumMemory kb="CUD" kxe="false">{min_memory}</MinimumMemory>')
    parts.append("  </PartitionMemoryConfiguration>")
    return "\n".join(parts)


def _processor_config(
    dedicated: bool,
    min_procs: float | None,
    desired_procs: float | None,
    max_procs: float | None,
    min_vcpus: int | None,
    desired_vcpus: int | None,
    max_vcpus: int | None,
    sharing_mode: str | None,
    uncapped: bool,
) -> str:
    """Build PartitionProcessorConfiguration.

    dedicated=True  -> DedicatedProcessorConfiguration (whole CPUs).
    dedicated=False -> SharedProcessorConfiguration (processing units) plus
                       virtual processor min/desired/max.
    For shared partitions, procs are processing units (may be fractional,
    e.g. 0.5); vcpus are the virtual processor counts (ints).
    """
    have_dedicated = dedicated and any(v is not None for v in (min_procs, desired_procs, max_procs))
    have_shared = (not dedicated) and any(
        v is not None for v in (min_procs, desired_procs, max_procs, min_vcpus, desired_vcpus, max_vcpus)
    )
    if not (have_dedicated or have_shared):
        return ""

    parts = ['  <PartitionProcessorConfiguration kb="CUD" kxe="false">', "    <Metadata><Atom/></Metadata>"]

    if dedicated:
        parts.append('    <DedicatedProcessorConfiguration kb="CUD" kxe="false">')
        parts.append("      <Metadata><Atom/></Metadata>")
        if desired_procs is not None:
            parts.append(f'      <DesiredProcessors kb="CUD" kxe="false">{int(desired_procs)}</DesiredProcessors>')
        if max_procs is not None:
            parts.append(f'      <MaximumProcessors kb="CUD" kxe="false">{int(max_procs)}</MaximumProcessors>')
        if min_procs is not None:
            parts.append(f'      <MinimumProcessors kb="CUD" kxe="false">{int(min_procs)}</MinimumProcessors>')
        parts.append("    </DedicatedProcessorConfiguration>")
        parts.append('    <HasDedicatedProcessors kb="CUD" kxe="false">true</HasDedicatedProcessors>')
        if sharing_mode:
            parts.append(f'    <SharingMode kb="CUD" kxe="false">{sharing_mode}</SharingMode>')
    else:
        parts.append('    <HasDedicatedProcessors kb="CUD" kxe="false">false</HasDedicatedProcessors>')
        parts.append('    <SharedProcessorConfiguration kb="CUD" kxe="false">')
        parts.append("      <Metadata><Atom/></Metadata>")

        def _sp(name: str, val):
            if val is not None:
                if isinstance(val, float) and val.is_integer():
                    val = int(val)
                parts.append(f'      <{name} kb="CUD" kxe="false">{val}</{name}>')

        _sp("DesiredProcessingUnits", desired_procs)
        _sp("MaximumProcessingUnits", max_procs)
        _sp("MinimumProcessingUnits", min_procs)
        _sp("DesiredVirtualProcessors", desired_vcpus)
        _sp("MaximumVirtualProcessors", max_vcpus)
        _sp("MinimumVirtualProcessors", min_vcpus)
        if not uncapped:
            parts.append('      <UncappedWeight kb="CUD" kxe="false">0</UncappedWeight>')
        parts.append("    </SharedProcessorConfiguration>")
        if uncapped:
            parts.append('    <SharingMode kb="CUD" kxe="false">uncapped</SharingMode>')
        elif sharing_mode:
            parts.append(f'    <SharingMode kb="CUD" kxe="false">{sharing_mode}</SharingMode>')
        else:
            parts.append('    <SharingMode kb="CUD" kxe="false">capped</SharingMode>')

    parts.append("  </PartitionProcessorConfiguration>")
    return "\n".join(parts)


def build_lpar_document(
    name: str | None,
    partition_type: str = "AIX/Linux",
    partition_id: int | None = None,
    min_memory: int | None = None,
    desired_memory: int | None = None,
    max_memory: int | None = None,
    dedicated: bool = False,
    min_procs: float | None = None,
    desired_procs: float | None = None,
    max_procs: float | None = None,
    min_vcpus: int | None = None,
    desired_vcpus: int | None = None,
    max_vcpus: int | None = None,
    sharing_mode: str | None = None,
    uncapped: bool = False,
) -> str:
    """Build a LogicalPartition document for PUT (create) or POST (modify).

    Memory values are in MiB. For a create, `name` is required; the rest are
    optional (the HMC supplies defaults). For a modify, supply only the fields
    to change (pass name=None to omit it).
    """
    if partition_type not in PARTITION_TYPES:
        raise ValueError(
            f"partition_type must be one of {PARTITION_TYPES}, got {partition_type!r}"
        )

    body_parts = ["  <Metadata><Atom/></Metadata>"]
    if partition_id is not None:
        body_parts.append(f'  <PartitionID kb="COD" kxe="false">{partition_id}</PartitionID>')

    mem = _memory_config(min_memory, desired_memory, max_memory)
    if mem:
        body_parts.append(mem)

    if name is not None:
        body_parts.append(f'  <PartitionName kb="CUR" kxe="false">{name}</PartitionName>')

    proc = _processor_config(
        dedicated, min_procs, desired_procs, max_procs,
        min_vcpus, desired_vcpus, max_vcpus, sharing_mode, uncapped,
    )
    if proc:
        body_parts.append(proc)

    body_parts.append(f'  <PartitionType kb="COD" kxe="false">{partition_type}</PartitionType>')

    body = "\n".join(body_parts)
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<LogicalPartition xmlns="{UOM_NS}" schemaVersion="V1_8_0">
{body}
</LogicalPartition>
"""


def build_vios_document(
    name: str,
    min_memory: int = 512,
    desired_memory: int = 4096,
    max_memory: int = 8192,
    desired_vcpus: int = 2,
    min_vcpus: int = 1,
    max_vcpus: int = 4,
    desired_procs: float = 0.5,
    min_procs: float = 0.1,
    max_procs: float = 1.0,
) -> str:
    """Build a LogicalPartition document for creating a Virtual IO Server.

    Wraps build_lpar_document with partition_type='Virtual IO Server' and
    shared-processor defaults appropriate for VIOS provisioning. All resource
    values are optional; the HMC supplies defaults for anything not set.
    Memory values are in MiB; procs are processing units (fractional ok).
    """
    return build_lpar_document(
        name=name,
        partition_type="Virtual IO Server",
        min_memory=min_memory,
        desired_memory=desired_memory,
        max_memory=max_memory,
        dedicated=False,
        min_procs=min_procs,
        desired_procs=desired_procs,
        max_procs=max_procs,
        min_vcpus=min_vcpus,
        desired_vcpus=desired_vcpus,
        max_vcpus=max_vcpus,
        uncapped=True,
    )


def build_dlpar_proc_document(
    desired_procs: float | None = None,
    min_procs: float | None = None,
    max_procs: float | None = None,
    desired_vcpus: int | None = None,
    min_vcpus: int | None = None,
    max_vcpus: int | None = None,
    dedicated: bool = False,
    uncapped: bool = True,
) -> str:
    """Minimal LogicalPartition document containing only PartitionProcessorConfiguration.

    Used for DLPAR processor hot-plug: POST to /rest/api/uom/LogicalPartition/{uuid}.
    On a running partition this applies immediately if RMC is active; otherwise the
    change is profile-only and takes effect on next activation.

    For shared partitions, procs are processing units (may be fractional, e.g. 0.5);
    vcpus are the virtual processor counts (ints).
    Set dedicated=True to assign whole CPUs; dedicated=False (default) for shared.
    """
    proc = _processor_config(
        dedicated, min_procs, desired_procs, max_procs,
        min_vcpus, desired_vcpus, max_vcpus, None, uncapped,
    )
    body = "  <Metadata><Atom/></Metadata>"
    if proc:
        body = body + "\n" + proc
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<LogicalPartition xmlns="{UOM_NS}" schemaVersion="V1_8_0">
{body}
</LogicalPartition>
"""


def build_dlpar_mem_document(
    desired_mem: int | None = None,
    min_mem: int | None = None,
    max_mem: int | None = None,
) -> str:
    """Minimal LogicalPartition document containing only PartitionMemoryConfiguration.

    Used for DLPAR memory hot-plug: POST to /rest/api/uom/LogicalPartition/{uuid}.
    On a running partition this applies immediately if RMC is active; otherwise the
    change is profile-only and takes effect on next activation.

    Memory values are in MiB.
    """
    mem = _memory_config(min_mem, desired_mem, max_mem)
    body = "  <Metadata><Atom/></Metadata>"
    if mem:
        body = body + "\n" + mem
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<LogicalPartition xmlns="{UOM_NS}" schemaVersion="V1_8_0">
{body}
</LogicalPartition>
"""


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
    slot = ""
    if slot_number is not None:
        slot = f'  <VirtualSlotNumber kb="CUD" kxe="false">{slot_number}</VirtualSlotNumber>\n'
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<VirtualSCSIClientAdapter xmlns="{UOM_NS}" schemaVersion="V1_8_0">
  <Metadata><Atom/></Metadata>
  <AdapterType kb="CUD" kxe="false">Client</AdapterType>
{slot}  <RemoteLogicalPartitionID kb="CUD" kxe="false">{vios_partition_id}</RemoteLogicalPartitionID>
  <RemoteSlotNumber kb="CUD" kxe="false">{vios_slot}</RemoteSlotNumber>
</VirtualSCSIClientAdapter>
"""


def build_vfc_adapter_document(
    vios_partition_id: int,
    vios_slot: int,
    slot_number: int | None = None,
) -> str:
    """Virtual Fibre Channel (NPIV) client adapter, paired to a VIOS.

    ConnectingPartitionID / ConnectingVirtualSlotNumber identify the VIOS and
    its server-side FC slot. The WWPNs are generated by the HMC on creation.
    """
    slot = ""
    if slot_number is not None:
        slot = f'  <VirtualSlotNumber kb="CUD" kxe="false">{slot_number}</VirtualSlotNumber>\n'
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<VirtualFibreChannelClientAdapter xmlns="{UOM_NS}" schemaVersion="V1_8_0">
  <Metadata><Atom/></Metadata>
  <AdapterType kb="CUD" kxe="false">Client</AdapterType>
{slot}  <ConnectingPartitionID kb="CUD" kxe="false">{vios_partition_id}</ConnectingPartitionID>
  <ConnectingVirtualSlotNumber kb="CUD" kxe="false">{vios_slot}</ConnectingVirtualSlotNumber>
</VirtualFibreChannelClientAdapter>
"""


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
        parts.append(f'  <VirtualSlotNumber kb="CUD" kxe="false">{slot_number}</VirtualSlotNumber>')
    if virtual_switch_id is not None:
        parts.append(f'  <VirtualSwitchID kb="CUD" kxe="false">{virtual_switch_id}</VirtualSwitchID>')
    parts.append(f'  <PortVLANID kb="CUD" kxe="false">{port_vlan_id}</PortVLANID>')
    if tagged:
        parts.append('  <IsTaggedVLAN kb="CUD" kxe="false">true</IsTaggedVLAN>')
    if mac_address:
        parts.append(f'  <MACAddress kb="CUD" kxe="false">{mac_address}</MACAddress>')
    body = "\n".join(parts)
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<ClientNetworkAdapter xmlns="{UOM_NS}" schemaVersion="V1_8_0">
{body}
</ClientNetworkAdapter>
"""


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


def build_volume_group_document(name: str, physical_volumes: list[str]) -> str:
    """Document to create a Volume Group from a set of physical volumes."""
    pvs = "\n".join(
        f'    <PhysicalVolume kb="CUD" kxe="false" schemaVersion="V1_8_0">\n'
        f'      <Metadata><Atom/></Metadata>\n'
        f'      <VolumeName kb="CUD" kxe="false">{pv}</VolumeName>\n'
        f'    </PhysicalVolume>'
        for pv in physical_volumes
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<VolumeGroup xmlns="{UOM_NS}" schemaVersion="V1_8_0">
  <Metadata><Atom/></Metadata>
  <GroupName kb="CUD" kxe="false">{name}</GroupName>
  <PhysicalVolumes kb="CUD" kxe="false" schemaVersion="V1_8_0">
    <Metadata><Atom/></Metadata>
{pvs}
  </PhysicalVolumes>
</VolumeGroup>
"""


def build_virtual_disk_document(disk_name: str, capacity_mb: int) -> str:
    """A VolumeGroup document carrying a new VirtualDisk (for create POST)."""
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<VolumeGroup xmlns="{UOM_NS}" schemaVersion="V1_8_0">
  <Metadata><Atom/></Metadata>
  <VirtualDisks kb="CUD" kxe="false" schemaVersion="V1_8_0">
    <Metadata><Atom/></Metadata>
    <VirtualDisk kb="CUD" kxe="false" schemaVersion="V1_8_0">
      <Metadata><Atom/></Metadata>
      <DiskName kb="CUD" kxe="false">{disk_name}</DiskName>
      <DiskCapacity kb="CUD" kxe="false">{capacity_mb}</DiskCapacity>
    </VirtualDisk>
  </VirtualDisks>
</VolumeGroup>
"""


def build_vscsi_mapping_document(
    storage_kind: str,
    storage_name: str,
    lpar_link: str,
    vios_lpar_link: str | None = None,
    target_device: str | None = None,
) -> str:
    """A VirtualIOServer document carrying a VirtualSCSIMapping (for POST).

    storage_kind is "PhysicalVolume" (whole disk) or "VirtualDisk" (a logical
    volume from a VG). storage_name is the device/disk name (e.g. hdisk5 or
    the DiskName). lpar_link is the Atom SELF href of the client LPAR the
    storage is mapped to. target_device optionally pins the vtscsi name.
    """
    if storage_kind not in ("PhysicalVolume", "VirtualDisk"):
        raise ValueError(f"storage_kind must be PhysicalVolume or VirtualDisk, got {storage_kind!r}")
    name_field = "VolumeName" if storage_kind == "PhysicalVolume" else "DiskName"
    target = ""
    if target_device:
        target = f'      <TargetDevice kb="CUD" kxe="false">{target_device}</TargetDevice>\n'
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<VirtualIOServer xmlns="{UOM_NS}" xmlns:atom="{ATOM_NS}" schemaVersion="V1_8_0">
  <Metadata><Atom/></Metadata>
  <VirtualSCSIMappings kb="CUD" kxe="false" schemaVersion="V1_8_0">
    <Metadata><Atom/></Metadata>
    <VirtualSCSIMapping kb="CUD" kxe="false" schemaVersion="V1_8_0">
      <Metadata><Atom/></Metadata>
      <Storage kb="CUD" kxe="false" schemaVersion="V1_8_0">
        <Metadata><Atom/></Metadata>
        <{storage_kind} kb="CUD" kxe="false" schemaVersion="V1_8_0">
          <Metadata><Atom/></Metadata>
          <{name_field} kb="CUD" kxe="false">{storage_name}</{name_field}>
        </{storage_kind}>
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


def build_virtual_network_document(
    name: str,
    vlan_id: int,
    vswitch_id: int,
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
<VirtualNetwork xmlns="{UOM_NS}" xmlns:atom="{ATOM_NS}" schemaVersion="V1_8_0">
  <Metadata><Atom/></Metadata>
{assoc}  <NetworkName kb="CUD" kxe="false">{name}</NetworkName>
  <NetworkVLANID kb="CUD" kxe="false">{vlan_id}</NetworkVLANID>
  <VswitchID kb="CUD" kxe="false">{vswitch_id}</VswitchID>
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


def build_media_repository_document(size_mb: int) -> str:
    """VolumeGroup document carrying a VirtualMediaRepository (create POST).

    The repository is always named VMLibrary; size_mb is RepositorySize.
    """
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<VolumeGroup xmlns="{UOM_NS}" schemaVersion="V1_8_0">
  <Metadata><Atom/></Metadata>
  <VirtualMediaRepository schemaVersion="V1_8_0">
    <Metadata><Atom/></Metadata>
    <RepositoryName kb="CUD" kxe="false">VMLibrary</RepositoryName>
    <RepositorySize kb="CUD" kxe="false">{size_mb}</RepositorySize>
  </VirtualMediaRepository>
</VolumeGroup>
"""


def build_virtual_optical_media_document(media_name: str, size_mb: int) -> str:
    """VolumeGroup document carrying a blank VirtualOpticalMedia (create POST).

    Only blank optical media can be created via the API; media_name is the
    file name (e.g. 'aix.iso'), size_mb is MediaSize.
    """
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<VolumeGroup xmlns="{UOM_NS}" schemaVersion="V1_8_0">
  <Metadata><Atom/></Metadata>
  <VirtualMediaRepository schemaVersion="V1_8_0">
    <Metadata><Atom/></Metadata>
    <VirtualOpticalMedia schemaVersion="V1_8_0">
      <Metadata><Atom/></Metadata>
      <MediaName kb="CUD" kxe="false">{media_name}</MediaName>
      <MediaSize kb="CUD" kxe="false">{size_mb}</MediaSize>
      <MediaType kb="CUD" kxe="false">BLANK</MediaType>
    </VirtualOpticalMedia>
  </VirtualMediaRepository>
</VolumeGroup>
"""


def build_media_repository_delete_document() -> str:
    """VolumeGroup document marking the VirtualMediaRepository for deletion (POST)."""
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<VolumeGroup xmlns="{UOM_NS}" schemaVersion="V1_8_0">
  <Metadata><Atom/></Metadata>
  <VirtualMediaRepository schemaVersion="V1_8_0" kb="CUD">
    <Metadata><Atom/></Metadata>
    <RepositoryName kb="CUD" kxe="false">VMLibrary</RepositoryName>
  </VirtualMediaRepository>
</VolumeGroup>
"""


# ====================================================================== #
# HMC User management (/rest/api/web/HmcUser)
#
# Create: POST /rest/api/web/HmcUser
# Modify: POST /rest/api/web/HmcUser/{name}
# Fields documented in ansible-power-hmc plugins/modules/hmc_user.py
# ====================================================================== #

WEB_NS = "http://www.ibm.com/xmlns/systems/power/firmware/web/mc/2012_10/"


def build_hmc_user_document(
    username: str | None = None,
    taskrole: str | None = None,
    password: str | None = None,
    description: str | None = None,
    pwage: int | None = None,
    enable: bool | None = None,
) -> str:
    """Build an HmcUser XML document for create (POST) or modify (POST).

    For create, username is required.  For modify, pass only the fields
    to change.  pwage is the password expiration in days (0 = never expires).
    enable controls whether the account is enabled (True) or disabled (False).
    """
    parts = ["  <Metadata><Atom/></Metadata>"]
    if username is not None:
        parts.append(f'  <UserID kb="CUR" kxe="false">{username}</UserID>')
    if taskrole is not None:
        parts.append(f'  <TaskRole kb="CUR" kxe="false">{taskrole}</TaskRole>')
    if password is not None:
        parts.append(f'  <Password kb="CUR" kxe="false">{password}</Password>')
    if description is not None:
        parts.append(f'  <Description kb="CUR" kxe="false">{description}</Description>')
    if pwage is not None:
        parts.append(f'  <PasswordAgePolicy kb="CUR" kxe="false">{pwage}</PasswordAgePolicy>')
    if enable is not None:
        val = "true" if enable else "false"
        parts.append(f'  <IsEnabled kb="CUR" kxe="false">{val}</IsEnabled>')
    body = "\n".join(parts)
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<HmcUser xmlns="{WEB_NS}" schemaVersion="V1_0">
{body}
</HmcUser>
"""
