from __future__ import annotations

from typing import Literal, get_args

from ..xmlutil import ATOM_NS, escapes_string_arguments
from .common import UOM_NS, document_envelope

StorageKind = Literal["PhysicalVolume", "VirtualDisk"]
STORAGE_KINDS = frozenset(get_args(StorageKind))


@escapes_string_arguments
def build_volume_group_document(name: str, physical_volumes: list[str]) -> str:
    """Document to create a Volume Group from a set of physical volumes."""
    pvs = "\n".join(
        f'    <PhysicalVolume schemaVersion="V1_0">\n'
        f"      <Metadata><Atom/></Metadata>\n"
        f'      <VolumeName kb="CUR" kxe="false">{pv}</VolumeName>\n'
        f"    </PhysicalVolume>"
        for pv in physical_volumes
    )
    body = f"""  <Metadata><Atom/></Metadata>
  <GroupName kb="CUD" kxe="false">{name}</GroupName>
  <PhysicalVolumes kb="CUD" kxe="false" schemaVersion="V1_0">
    <Metadata><Atom/></Metadata>
{pvs}
  </PhysicalVolumes>"""
    return document_envelope("VolumeGroup", body)


@escapes_string_arguments
def build_virtual_disk_document(disk_name: str, capacity_mib: int) -> str:
    """A VolumeGroup document carrying a new VirtualDisk (for create POST)."""
    if capacity_mib <= 0 or capacity_mib % 1024:
        raise ValueError("capacity_mib must be a positive multiple of 1024")
    capacity_gib = capacity_mib // 1024
    body = f"""  <Metadata><Atom/></Metadata>
  <VirtualDisks kb="CUD" kxe="false" schemaVersion="V1_0">
    <Metadata><Atom/></Metadata>
    <VirtualDisk schemaVersion="V1_0">
      <Metadata><Atom/></Metadata>
      <DiskCapacity kb="CUR" kxe="false">{capacity_gib}</DiskCapacity>
      <DiskName kb="CUR" kxe="false">{disk_name}</DiskName>
    </VirtualDisk>
  </VirtualDisks>"""
    return document_envelope("VolumeGroup", body)


_TARGET_DEVICE_ELEMENTS = {
    "PhysicalVolume": "PhysicalVolumeVirtualTargetDevice",
    "VirtualDisk": "LogicalVolumeVirtualTargetDevice",
    "VirtualOpticalMedia": "VirtualOpticalTargetDevice",
}


def _target_device(element: str, name: str | None) -> str:
    """Typed V10R3 TargetDevice pinning the vtscsi name; empty when *name* is unset."""
    if not name:
        return ""
    return f"""
      <TargetDevice kb="CUR" kxe="false">
        <{element} schemaVersion="V1_0">
          <Metadata><Atom/></Metadata>
          <TargetName kb="CUR" kxe="false">{name}</TargetName>
        </{element}>
      </TargetDevice>"""


def _mapping_document(storage_xml: str, target_xml: str, lpar_link: str) -> str:
    """VirtualIOServer document with one VirtualSCSIMapping, in the V10R3 child order.

    Arguments are already escaped by the public builders; this helper adds no values.
    """
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<VirtualIOServer xmlns="{UOM_NS}" xmlns:atom="{ATOM_NS}" schemaVersion="V1_0">
  <Metadata><Atom/></Metadata>
  <VirtualSCSIMappings kb="CUD" kxe="false" schemaVersion="V1_0">
    <Metadata><Atom/></Metadata>
    <VirtualSCSIMapping schemaVersion="V1_0">
      <Metadata><Atom/></Metadata>
      <AssociatedLogicalPartition kb="CUR" kxe="false" href="{lpar_link}" rel="related"/>
      <Storage kb="CUR" kxe="false">
{storage_xml}
      </Storage>{target_xml}
    </VirtualSCSIMapping>
  </VirtualSCSIMappings>
</VirtualIOServer>
"""


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
    storage = f"""        <{storage_kind} schemaVersion="V1_0">
          <Metadata><Atom/></Metadata>
          <{name_field} kb="CUR" kxe="false">{storage_name}</{name_field}>
        </{storage_kind}>"""
    target = _target_device(_TARGET_DEVICE_ELEMENTS[storage_kind], target_device)
    return _mapping_document(storage, target, lpar_link)


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
    storage = f"""        <VirtualOpticalMedia schemaVersion="V1_0">
          <Metadata><Atom/></Metadata>
          <MediaName kb="CUR" kxe="false">{media_name}</MediaName>
        </VirtualOpticalMedia>"""
    target = _target_device(_TARGET_DEVICE_ELEMENTS["VirtualOpticalMedia"], target_device)
    return _mapping_document(storage, target, lpar_link)


# Virtual Network (child of ManagedSystem)
#
# Create: PUT /rest/api/uom/ManagedSystem/{sys}/VirtualNetwork
# Fields: NetworkName, NetworkVLANID, VswitchID, TaggedNetwork, and an
# AssociatedSwitch Atom link to the backing VirtualSwitch.


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
  <NetworkVLANID kb="COD" kxe="false">{vlan_id}</NetworkVLANID>
  <VswitchID kb="ROR" kxe="false">{virtual_switch_id}</VswitchID>
  <TaggedNetwork kb="COD" kxe="false">{tagged_str}</TaggedNetwork>
</VirtualNetwork>
"""


# Virtual Media Repository / Virtual Optical Media
#
# Both are operations via POST on a VolumeGroup (the repository lives on the
# "VMLibrary" volume group of a VIOS). The repository name is always
# "VMLibrary"; only BLANK optical media can be created via this API.


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
    return document_envelope("VolumeGroup", body)


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
    return document_envelope("VolumeGroup", body)


@escapes_string_arguments
def build_virtual_disk_delete_document(disk_name: str) -> str:
    """VolumeGroup document marking a VirtualDisk for deletion (POST)."""
    body = f"""  <Metadata><Atom/></Metadata>
  <VirtualDisks schemaVersion="V1_0" kb="CUD">
    <Metadata><Atom/></Metadata>
    <VirtualDisk schemaVersion="V1_0">
      <Metadata><Atom/></Metadata>
      <VolumeGroupName kb="CUD" kxe="false">{disk_name}</VolumeGroupName>
    </VirtualDisk>
  </VirtualDisks>"""
    return document_envelope("VolumeGroup", body)


# Brokered file upload / ISO import (ADR 0031)
#
# Create:  POST /rest/api/uom/VirtualIOServer/{uuid}/VolumeGroup/{uuid}
#          with a BrokeredFile document; the broker URI comes back in the
#          Location header.
# Import:  POST to the same path with a LinkedVirtualOpticalMedia document
#          naming that broker URI.
#
# Neither document carries schemaVersion, so they render their own envelope
# rather than going through an envelope helper. Both are transport
# primitives for #203's future public API and are not exposed today.


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


# Session logon (/rest/api/web/Logon)
#
# Authenticate: PUT /rest/api/web/Logon with a LogonRequest document; the
# response carries the X-API-Session token.
