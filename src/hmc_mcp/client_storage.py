"""HMCClient storage mixin.

The full client is assembled in :mod:`hmc_mcp.client` by inheriting every
domain mixin; this module only defines methods for storage.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from .client_contracts import StorageClient
from .client_parse import _parse_feed
from .errors import HMCError
from .documents import (
    StorageKind,
    build_virtual_disk_delete_document,
    build_virtual_disk_document,
    build_volume_group_document,
    build_vscsi_mapping_document,
)

import re as _re

# HMC UOM namespace — used in read-modify-write VolumeGroup operations.
_UOM_NS = "http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/"
_ATOM_NS = "http://www.w3.org/2005/Atom"


def _extract_system_uuid_from_vios_xml(vios_xml: str) -> str:
    """Extract the first ManagedSystem UUID from a VIOS response."""
    match = _re.search(
        r"/rest/api/uom/ManagedSystem/([0-9a-fA-F-]{36})",
        vios_xml,
    )
    if not match:
        raise HMCError(
            "Cannot find ManagedSystem UUID in VirtualIOServer document. "
            "The VIOS response is missing AssociatedManagedSystem href.",
            200,
            vios_xml[:500],
        )
    return match.group(1)


def _find_vios_element(root: ET.Element, vios_uuid: str) -> ET.Element:
    """Return the one VIOS resource and reject ambiguous or mismatched documents."""
    tag = f"{{{_UOM_NS}}}VirtualIOServer"
    resources = ([root] if root.tag == tag else []) + root.findall(f".//{tag}")
    if len(resources) != 1:
        raise HMCError(
            f"VirtualIOServer GET response contained {len(resources)} VIOS resources; "
            "expected exactly one",
            200,
            ET.tostring(root, encoding="unicode")[:500],
        )
    vios_elem = resources[0]
    identities = vios_elem.findall(f"{{{_UOM_NS}}}UUID")
    if len(identities) != 1 or (identities[0].text or "").strip() != vios_uuid:
        raise HMCError(
            f"VirtualIOServer response identity does not match {vios_uuid!r}",
            200,
            ET.tostring(vios_elem, encoding="unicode")[:500],
        )
    return vios_elem


def _extract_system_uuid_from_vios(vios_elem: ET.Element) -> str:
    """Extract the exact ManagedSystem UUID associated with one VIOS element."""
    links = vios_elem.findall(f"{{{_UOM_NS}}}AssociatedManagedSystem")
    if len(links) != 1:
        raise HMCError(
            "VirtualIOServer must have exactly one AssociatedManagedSystem link",
            200,
            ET.tostring(vios_elem, encoding="unicode")[:500],
        )
    href = links[0].get("href", "")
    match = _re.fullmatch(
        r"(?:https?://[^/]+)?/rest/api/uom/ManagedSystem/"
        r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})/?",
        href,
    )
    if not match:
        raise HMCError(
            "AssociatedManagedSystem href does not contain an exact ManagedSystem UUID",
            200,
            repr(href),
        )
    return match.group(1)


class StorageMixin:
    # ------------------------------------------------------------------ #
    # Virtual storage (children of VirtualIOServer)
    # ------------------------------------------------------------------ #
    def get_lpar_link(self: StorageClient, lpar_uuid: str) -> str:
        """Atom SELF href for an LPAR (used when building mappings)."""
        return f"{self._rest_base_url}/rest/api/uom/LogicalPartition/{lpar_uuid}"

    async def list_volume_groups(
        self: StorageClient, vios_uuid: str
    ) -> list[dict[str, Any]]:
        """List Volume Groups on a VIOS (free space, PVs, virtual disks).

        The VolumeGroup endpoint returns HTTP 204 when X-HMC-Schema-Version is
        present, so we deliberately omit the schema-version header here.
        """
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup"
        xml = await self._get(path, "VolumeGroup", include_schema_version=False)
        return _parse_feed(xml, path) if xml else []

    async def get_volume_group(
        self: StorageClient, vios_uuid: str, vg_uuid: str
    ) -> dict[str, Any] | None:
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}"
        xml = await self._get(path, "VolumeGroup", include_schema_version=False)
        if not xml:
            return None
        entries = _parse_feed(xml, path)
        if not entries:
            return None
        entry = entries[0]
        resource = entry.get("Resource")
        if not isinstance(resource, dict) or "VirtualMediaRepository" not in resource:
            return None
        return entry

    async def create_volume_group(
        self: StorageClient,
        vios_uuid: str,
        name: str,
        physical_volumes: list[str],
    ) -> dict[str, Any] | None:
        """Create a Volume Group on a VIOS from physical volumes (e.g. ['hdisk10'])."""

        xml = build_volume_group_document(name, physical_volumes)
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup"
        resp = await self._put(path, xml, resource_type="VolumeGroup", include_schema_version=False)
        entries = _parse_feed(resp, path) if resp else []
        return entries[0] if entries else None

    async def create_virtual_disk(
        self: StorageClient,
        vios_uuid: str,
        vg_uuid: str,
        disk_name: str,
        capacity_mib: int,
    ) -> dict[str, Any] | None:
        """Create a Virtual Disk (logical volume) in a Volume Group.

        The VolumeGroup POST endpoint returns HTTP 406 when X-HMC-Schema-Version
        is present on some HMC firmware (same behaviour as the GET), so we omit
        the schema-version header here.
        """

        xml = build_virtual_disk_document(disk_name, capacity_mib)
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}"
        resp = await self._post(
            path, xml, resource_type="VolumeGroup", include_schema_version=False
        )
        entries = _parse_feed(resp, path) if resp else []
        return entries[0] if entries else None

    async def delete_virtual_disk(
        self: StorageClient, vios_uuid: str, vg_uuid: str, disk_name: str
    ) -> dict[str, Any] | None:
        """Delete a Virtual Disk (logical volume) from a Volume Group.

        The VolumeGroup POST endpoint returns HTTP 406 when X-HMC-Schema-Version
        is present on some HMC firmware (same behaviour as the GET), so we omit
        the schema-version header here.
        """

        xml = build_virtual_disk_delete_document(disk_name)
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}"
        resp = await self._post(
            path, xml, resource_type="VolumeGroup", include_schema_version=False
        )
        entries = _parse_feed(resp, path) if resp else []
        return entries[0] if entries else None

    async def map_storage_to_lpar(
        self: StorageClient,
        vios_uuid: str,
        storage_kind: StorageKind,
        storage_name: str,
        lpar_uuid: str,
        target_device: str | None = None,
    ) -> dict[str, Any] | None:
        """Create a VirtualSCSIMapping connecting backing storage to an LPAR.

        storage_kind is "PhysicalVolume" (whole hdisk) or "VirtualDisk" (a
        logical volume created with create_virtual_disk). storage_name is the
        device or disk name. lpar_uuid is the client partition to attach to.

        Omits X-HMC-Schema-Version for the same reason as the VolumeGroup
        endpoints — the schema-version header causes HTTP 406 on some firmware.
        """

        lpar_link = self.get_lpar_link(lpar_uuid)
        xml = build_vscsi_mapping_document(
            storage_kind, storage_name, lpar_link, target_device=target_device
        )
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}"
        resp = await self._post(
            path, xml, resource_type="VirtualIOServer", include_schema_version=False
        )
        entries = _parse_feed(resp, path) if resp else []
        return entries[0] if entries else None

    # ------------------------------------------------------------------ #
    # Storage Mapping Inventory and Detach
    # ------------------------------------------------------------------ #
    async def list_storage_mappings(
        self: StorageClient, vios_uuid: str, lpar_uuid: str | None = None
    ) -> list[dict[str, Any]]:
        """List VirtualSCSIMappings on a VIOS, optionally filtered by LPAR.

        Returns mappings with backing storage details (PhysicalVolume or VirtualDisk)
        and client LPAR information. Use lpar_uuid to scope mappings to a single LPAR.

        Requests the documented ``ViosSCSIMapping`` extended group.
        """
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}?group=ViosSCSIMapping"
        xml = await self._get(path, "VirtualIOServer")
        if not xml:
            return []

        entries = _parse_feed(xml, path)
        if not entries:
            return []

        detail = entries[0]
        mappings = (
            detail.get("Resource", {})
            .get("VirtualSCSIMappings", {})
        )
        if not isinstance(mappings, dict):
            return []
        mappings = mappings.get("VirtualSCSIMapping", [])
        if not isinstance(mappings, list):
            mappings = [mappings] if mappings else []

        if lpar_uuid:
            expected_link = f"/rest/api/uom/LogicalPartition/{lpar_uuid}"
            mappings = [
                m for m in mappings
                if isinstance(m, dict) and m.get("AssociatedLogicalPartition", {}).get("href") == expected_link
            ]

        return mappings if isinstance(mappings, list) else [mappings]

    async def delete_storage_mapping(
        self: StorageClient, vios_uuid: str, mapping_uuid: str
    ) -> None:
        """Detach one mapping through its parent VirtualIOServer document."""
        if not mapping_uuid:
            raise HMCError("Storage mapping UUID must not be empty")

        get_path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}"
        vios_xml = await self._get(
            get_path, "VirtualIOServer", include_schema_version=False
        )
        if not vios_xml:
            raise HMCError(f"GET {get_path} returned empty response", 200, "")
        try:
            root = ET.fromstring(vios_xml)
        except ET.ParseError as exc:
            raise HMCError(
                "VirtualIOServer GET response is not valid XML", 200, vios_xml
            ) from exc

        vios_elem = _find_vios_element(root, vios_uuid)
        mappings = vios_elem.find(f"{{{_UOM_NS}}}VirtualSCSIMappings")
        if mappings is None:
            raise HMCError(
                f"Storage mapping {mapping_uuid!r} not found on VIOS {vios_uuid!r}"
            )
        identities: dict[str, ET.Element] = {}
        for mapping in mappings.findall(f"{{{_UOM_NS}}}VirtualSCSIMapping"):
            uuid_elements = mapping.findall(f"{{{_UOM_NS}}}UUID")
            if len(uuid_elements) != 1 or not (uuid_elements[0].text or "").strip():
                raise HMCError("VirtualSCSIMapping has an invalid UUID identity")
            identity = (uuid_elements[0].text or "").strip()
            if identity in identities:
                raise HMCError(
                    f"VirtualSCSIMapping UUID {identity!r} is duplicated; "
                    "refusing an ambiguous detach"
                )
            identities[identity] = mapping
        target = identities.get(mapping_uuid)
        if target is None:
            raise HMCError(
                f"Storage mapping {mapping_uuid!r} not found on VIOS {vios_uuid!r}"
            )

        mappings.remove(target)
        system_uuid = _extract_system_uuid_from_vios(vios_elem)
        post_path = (
            f"/rest/api/uom/ManagedSystem/{system_uuid}/VirtualIOServer/{vios_uuid}"
        )
        response = await self._request(
            "POST",
            post_path,
            content=ET.tostring(vios_elem, encoding="unicode"),
            headers={
                "Accept": "*/*",
                "Content-Type": "application/vnd.ibm.powervm.uom+xml; type=VirtualIOServer",
            },
        )
        if response.status_code not in (200, 201, 202):
            raise HMCError(
                f"POST {post_path} failed", response.status_code, response.text
            )

    # ------------------------------------------------------------------ #
    # Virtual Media Repository / Virtual Optical Media (VolumeGroup POSTs)
    # ------------------------------------------------------------------ #
    # ------------------------------------------------------------------ #
    # Read-modify-write helpers for VolumeGroup media-repository operations
    # ------------------------------------------------------------------ #

    async def _get_vg_raw_xml(
        self: StorageClient, vios_uuid: str, vg_uuid: str
    ) -> tuple[str, ET.Element]:
        """GET the full VolumeGroup XML and return (url, VolumeGroup element).

        Parses the Atom feed to extract the single VolumeGroup element.
        The returned ET.Element is a copy with namespace prefixes re-registered
        so subsequent serialisation round-trips cleanly.
        """
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}"
        raw = await self._get(path, "VolumeGroup", include_schema_version=False)
        if not raw:
            raise HMCError(f"GET {path} returned empty body", 200, "")

        # Register the UOM namespace prefix so serialised XML uses the correct
        # namespace URI rather than ns0/ns1 auto-generated prefixes.
        ET.register_namespace("", _UOM_NS)
        ET.register_namespace("atom", _ATOM_NS)

        root = ET.fromstring(raw)
        # Response is either an Atom feed (feed/entry/content/VolumeGroup)
        # or a bare VolumeGroup element.
        ns = {"atom": _ATOM_NS, "uom": _UOM_NS}
        vg_elem = (
            root.find(".//atom:entry/atom:content/uom:VolumeGroup", ns)
            or root.find(".//uom:VolumeGroup", ns)
            or root.find(".//VolumeGroup")
        )
        if vg_elem is None:
            # Fallback: the root itself may be VolumeGroup
            local = root.tag.split("}")[-1] if "}" in root.tag else root.tag
            if local == "VolumeGroup":
                vg_elem = root
            else:
                raise HMCError(
                    f"GET {path} response contains no VolumeGroup element",
                    200,
                    raw[:500],
                )
        url = f"{self._rest_base_url}{path}"
        return url, vg_elem

    async def _post_vg_xml(
        self: StorageClient, vios_uuid: str, vg_uuid: str, vg_elem: ET.Element
    ) -> dict[str, Any] | None:
        """POST the serialised VolumeGroup element and return the parsed response.

        Uses Accept: */* to avoid HTTP 406 content-negotiation failures on V10R3
        firmware, while still declaring the correct Content-Type so the HMC can
        parse the body.

        The HMC accepts the VolumeGroup element directly as the POST body — not
        wrapped in an Atom feed — with Content-Type: type=VolumeGroup.
        """
        MEDIA_UOM = "application/vnd.ibm.powervm.uom+xml"
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}"
        headers = {
            "Accept": "*/*",
            "Content-Type": f"{MEDIA_UOM}; type=VolumeGroup",
        }
        body = ET.tostring(vg_elem, encoding="unicode", xml_declaration=False)
        resp = await self._request("POST", path, content=body, headers=headers)
        if resp.status_code not in (200, 201, 202):
            raise HMCError(f"POST {path} failed", resp.status_code, resp.text)
        entries = _parse_feed(resp.text, path) if resp.text else []
        return entries[0] if entries else None

    def _find_or_create_media_repos(self, vg_elem: ET.Element) -> ET.Element:
        """Return the MediaRepositories element from *vg_elem*, creating it if absent."""
        tag = f"{{{_UOM_NS}}}MediaRepositories"
        mr = vg_elem.find(f".//{tag}")
        if mr is None:
            mr = ET.SubElement(vg_elem, tag)
            meta = ET.SubElement(mr, f"{{{_UOM_NS}}}Metadata")
            ET.SubElement(meta, f"{{{_UOM_NS}}}Atom")
        return mr

    def _find_vmlib(self, vg_elem: ET.Element) -> ET.Element | None:
        """Return the VirtualMediaRepository (VMLibrary) element, or None."""
        return vg_elem.find(
            f".//{{{_UOM_NS}}}VirtualMediaRepository"
        ) or vg_elem.find(".//VirtualMediaRepository")

    def _build_mr_element(self, size_mib: int) -> ET.Element:
        """Build a MediaRepositories element with a VMLibrary inside.

        The HMC XSD requires schemaVersion on MediaRepositories and its children.
        """
        mr = ET.Element(
            f"{{{_UOM_NS}}}MediaRepositories", attrib={"schemaVersion": "V1_0"}
        )
        meta_mr = ET.SubElement(mr, f"{{{_UOM_NS}}}Metadata")
        ET.SubElement(meta_mr, f"{{{_UOM_NS}}}Atom")
        vmlib = ET.SubElement(
            mr,
            f"{{{_UOM_NS}}}VirtualMediaRepository",
            attrib={"schemaVersion": "V1_0"},
        )
        meta_vmlib = ET.SubElement(vmlib, f"{{{_UOM_NS}}}Metadata")
        ET.SubElement(meta_vmlib, f"{{{_UOM_NS}}}Atom")
        name_el = ET.SubElement(vmlib, f"{{{_UOM_NS}}}RepositoryName")
        name_el.text = "VMLibrary"
        size_el = ET.SubElement(vmlib, f"{{{_UOM_NS}}}RepositorySize")
        size_el.text = str(size_mib)
        return mr

    def _insert_mr_at_correct_position(
        self, vg_elem: ET.Element, mr_elem: ET.Element
    ) -> None:
        """Insert *mr_elem* into *vg_elem* in the correct schema position.

        The HMC VolumeGroup XSD sequence (confirmed against the live REST GET):
          Metadata, AvailableSize, FreeSpace, GroupCapacity, GroupName,
          GroupSerialID, MaximumLogicalVolumes,
          **MediaRepositories** ← here,
          PhysicalVolumes, UniqueDeviceID, VirtualDisks.

        We insert MediaRepositories immediately before PhysicalVolumes (or,
        if absent, after MaximumLogicalVolumes). If neither anchor is present
        we fall back to appending at the end.
        """
        children = list(vg_elem)
        pvs_tag = f"{{{_UOM_NS}}}PhysicalVolumes"
        max_lv_tag = f"{{{_UOM_NS}}}MaximumLogicalVolumes"

        def _local(tag: str) -> str:
            return tag.split("}")[-1]

        # Prefer inserting before PhysicalVolumes.
        insert_at = next(
            (
                i
                for i, child in enumerate(children)
                if child.tag == pvs_tag or _local(child.tag) == "PhysicalVolumes"
            ),
            None,
        )
        if insert_at is None:
            # Fall back: insert after MaximumLogicalVolumes.
            insert_at = next(
                (
                    i + 1
                    for i, child in enumerate(children)
                    if child.tag == max_lv_tag
                    or _local(child.tag) == "MaximumLogicalVolumes"
                ),
                None,
            )
        if insert_at is not None:
            vg_elem.insert(insert_at, mr_elem)
        else:
            vg_elem.append(mr_elem)

    async def create_media_repository(
        self: StorageClient, vios_uuid: str, vg_uuid: str, size_mib: int
    ) -> dict[str, Any] | None:
        """Create the Virtual Media Repository (VMLibrary) on a Volume Group.

        Uses a read-modify-write pattern: GET the full VolumeGroup XML, inject a
        VirtualMediaRepository node before VirtualDisks (per the HMC XSD sequence),
        then POST the modified XML back. This is the only approach that works on HMC
        V10R3 firmware (minimal-payload POSTs return HTTP 406 or 500).
        """
        _, vg_elem = await self._get_vg_raw_xml(vios_uuid, vg_uuid)

        # Remove any existing VMLibrary so creation is idempotent.
        mr_tag = f"{{{_UOM_NS}}}MediaRepositories"
        existing_mr = vg_elem.find(f".//{mr_tag}")
        if existing_mr is not None:
            vg_elem.remove(existing_mr)

        # Build the MediaRepositories block and insert in the correct schema position.
        mr = self._build_mr_element(size_mib)
        self._insert_mr_at_correct_position(vg_elem, mr)

        return await self._post_vg_xml(vios_uuid, vg_uuid, vg_elem)

    async def create_optical_media(
        self: StorageClient,
        vios_uuid: str,
        vg_uuid: str,
        media_name: str,
        size_mib: int,
    ) -> dict[str, Any] | None:
        """Create a blank VirtualOpticalMedia (ISO container) in the repository.

        Uses a read-modify-write pattern: GET the full VolumeGroup XML, inject a
        VirtualOpticalMedia node into the OpticalMedia container inside the
        VirtualMediaRepository, then POST the modified XML back.

        The HMC XSD structure inside VirtualMediaRepository is:
          Metadata, OpticalMedia (container for VirtualOpticalMedia entries),
          RepositoryName, RepositorySize.
        """
        _, vg_elem = await self._get_vg_raw_xml(vios_uuid, vg_uuid)

        vmlib = self._find_vmlib(vg_elem)
        if vmlib is None:
            raise HMCError(
                "No VirtualMediaRepository found in VolumeGroup — "
                "call create_media_repository first",
                404,
                "",
            )

        # VirtualOpticalMedia entries live inside an <OpticalMedia> container,
        # not directly inside VirtualMediaRepository.
        opt_media_tag = f"{{{_UOM_NS}}}OpticalMedia"
        opt_media = vmlib.find(opt_media_tag) or vmlib.find(".//OpticalMedia")
        if opt_media is None:
            # Create the OpticalMedia container before RepositoryName.
            repo_name_tag = f"{{{_UOM_NS}}}RepositoryName"
            repo_name_idx = next(
                (
                    i
                    for i, c in enumerate(list(vmlib))
                    if c.tag == repo_name_tag
                    or c.tag.split("}")[-1] == "RepositoryName"
                ),
                None,
            )
            opt_media = ET.Element(
                opt_media_tag, attrib={"schemaVersion": "V1_0"}
            )
            if repo_name_idx is not None:
                vmlib.insert(repo_name_idx, opt_media)
            else:
                vmlib.append(opt_media)

        vom_tag = f"{{{_UOM_NS}}}VirtualOpticalMedia"
        vom = ET.SubElement(opt_media, vom_tag, attrib={"schemaVersion": "V1_0"})
        meta = ET.SubElement(vom, f"{{{_UOM_NS}}}Metadata")
        ET.SubElement(meta, f"{{{_UOM_NS}}}Atom")
        n = ET.SubElement(vom, f"{{{_UOM_NS}}}MediaName")
        n.text = media_name
        # The HMC XSD uses 'Size' (not 'MediaSize') for the capacity field.
        s = ET.SubElement(vom, f"{{{_UOM_NS}}}Size")
        s.text = str(size_mib)
        t = ET.SubElement(vom, f"{{{_UOM_NS}}}MountType")
        t.text = "rw"

        return await self._post_vg_xml(vios_uuid, vg_uuid, vg_elem)

    async def delete_media_repository(
        self: StorageClient, vios_uuid: str, vg_uuid: str
    ) -> dict[str, Any] | None:
        """Delete the Virtual Media Repository (VMLibrary) from a Volume Group.

        Uses a read-modify-write pattern: GET the full VolumeGroup XML, remove the
        MediaRepositories block, then POST the modified XML back.
        """
        _, vg_elem = await self._get_vg_raw_xml(vios_uuid, vg_uuid)

        mr_tag = f"{{{_UOM_NS}}}MediaRepositories"
        mr = vg_elem.find(f".//{mr_tag}")
        if mr is None:
            # Nothing to delete — treat as success.
            return None
        vg_elem.remove(mr)

        return await self._post_vg_xml(vios_uuid, vg_uuid, vg_elem)

    async def delete_optical_media(
        self: StorageClient, vios_uuid: str, vg_uuid: str, media_name: str
    ) -> dict[str, Any] | None:
        """Delete a VirtualOpticalMedia (ISO image) from the media repository.

        Uses a read-modify-write pattern: GET the full VolumeGroup XML, remove the
        named VirtualOpticalMedia node from the OpticalMedia container, then POST back.
        """
        _, vg_elem = await self._get_vg_raw_xml(vios_uuid, vg_uuid)

        vmlib = self._find_vmlib(vg_elem)
        if vmlib is None:
            return None  # Nothing to remove.

        # VirtualOpticalMedia lives inside the OpticalMedia container.
        opt_media_tag = f"{{{_UOM_NS}}}OpticalMedia"
        vom_tag = f"{{{_UOM_NS}}}VirtualOpticalMedia"
        name_tag = f"{{{_UOM_NS}}}MediaName"

        # Search the OpticalMedia container first, then fall back to direct children.
        opt_media = vmlib.find(opt_media_tag) or vmlib.find(".//OpticalMedia")
        search_in = opt_media if opt_media is not None else vmlib

        to_remove: ET.Element | None = None
        for vom in list(search_in.findall(vom_tag)):
            n = vom.find(name_tag)
            if n is not None and n.text == media_name:
                to_remove = vom
                break
        if to_remove is None:
            return None  # Already gone.
        search_in.remove(to_remove)

        return await self._post_vg_xml(vios_uuid, vg_uuid, vg_elem)

    async def get_media_repository(
        self: StorageClient, vios_uuid: str, vg_uuid: str
    ) -> dict[str, Any] | None:
        """Get the Virtual Media Repository (VMLibrary) from a Volume Group.

        Returns the repository with capacity (RepositorySize) and optionally
        embedded VirtualOpticalMedia entries if present. Returns None if the
        Volume Group does not exist or has no media repository.
        """
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}"
        try:
            xml = await self._get(path, "VolumeGroup", include_schema_version=False)
        except HMCError as exc:
            if exc.status_code == 404:
                return None
            raise
        if not xml:
            return None
        entries = _parse_feed(xml, path)
        return entries[0] if entries else None

    async def list_optical_media(
        self: StorageClient, vios_uuid: str, vg_uuid: str
    ) -> list[dict[str, Any]]:
        """List Virtual Optical Media in the Virtual Media Repository.

        Returns a list of optical media entries (ISO containers) with their
        MediaName, MediaSize, and MediaType. Returns empty list if the
        Volume Group does not exist or has no media repository.
        """
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}"
        try:
            xml = await self._get(path, "VolumeGroup", include_schema_version=False)
        except HMCError as exc:
            if exc.status_code == 404:
                return []
            raise
        if not xml:
            return []

        entries = _parse_feed(xml, path)
        if not entries:
            return []

        # Extract VirtualOpticalMedia entries from the VolumeGroup response.
        # The HMC V10R3 structure is:
        #   Resource.MediaRepositories.VirtualMediaRepository.OpticalMedia.VirtualOpticalMedia
        # Older firmware may use a bare path without the wrappers.
        optical_media: list[dict[str, Any]] = []
        for entry in entries:
            resource = entry.get("Resource", {})
            # Try wrapper path first, then bare path.
            mr_container = resource.get("MediaRepositories") or resource
            repo = mr_container.get("VirtualMediaRepository", {})
            if not isinstance(repo, dict):
                repo = {}
            # V10R3+: VirtualOpticalMedia entries are inside an OpticalMedia container.
            opt_media_container = repo.get("OpticalMedia", repo)
            if not isinstance(opt_media_container, dict):
                opt_media_container = repo
            media_list = opt_media_container.get("VirtualOpticalMedia", [])
            if isinstance(media_list, list):
                optical_media.extend(media_list)
            elif isinstance(media_list, dict):
                optical_media.append(media_list)

        return optical_media
    # ------------------------------------------------------------------ #
    # Virtual Optical Mapping (VirtualSCSIMapping for VirtualOpticalMedia)
    # ------------------------------------------------------------------ #
    async def list_optical_mappings(
        self: StorageClient, vios_uuid: str, lpar_uuid: str | None = None
    ) -> list[dict[str, Any]]:
        """List VirtualSCSIMappings for optical media on a VIOS, optionally filtered by LPAR.

        Returns only mappings that reference VirtualOpticalMedia backing, with media
        details and client LPAR information. Use lpar_uuid to scope mappings to a single LPAR.
        """
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}?group=ViosSCSIMapping"
        xml = await self._get(path, "VirtualIOServer")
        if not xml:
            return []

        entries = _parse_feed(xml, path)
        if not entries:
            return []

        detail = entries[0]
        mappings = (
            detail.get("Resource", {})
            .get("VirtualSCSIMappings", {})
        )
        if not isinstance(mappings, dict):
            return []
        mappings = mappings.get("VirtualSCSIMapping", [])
        if not isinstance(mappings, list):
            mappings = [mappings] if mappings else []

        # Filter for optical mappings only (backed by VirtualOpticalMedia)
        optical_mappings = []
        for m in mappings:
            if not isinstance(m, dict):
                continue

            # Check if backed by VirtualOpticalMedia
            storage = m.get("Storage", {})
            if "VirtualOpticalMedia" in storage:
                optical_mappings.append(m)

        # Filter by LPAR if specified
        if lpar_uuid:
            expected_link = f"/rest/api/uom/LogicalPartition/{lpar_uuid}"
            optical_mappings = [
                m for m in optical_mappings
                if isinstance(m, dict) and m.get("AssociatedLogicalPartition", {}).get("href") == expected_link
            ]

        return optical_mappings if isinstance(optical_mappings, list) else [optical_mappings]

    async def create_optical_mapping(
        self: StorageClient, vios_uuid: str, media_name: str, lpar_uuid: str,
        target_device: str | None = None
    ) -> dict[str, Any] | None:
        """Create a VirtualSCSIMapping for optical media (mount ISO to LPAR).

        Uses a read-modify-write pattern: GETs the full VirtualIOServer document,
        appends a VirtualSCSIMapping entry for the named optical media, then POSTs
        the modified document back to the system-scoped VIOS endpoint.

        The system UUID is discovered from the AssociatedManagedSystem link in the
        VIOS document — no additional parameter is required.

        Returns the new VirtualSCSIMapping entry if it can be located in the
        response feed, else None.
        """
        # Step 1 — GET the full VIOS document (without schema version to avoid 406)
        get_path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}"
        vios_xml = await self._get(get_path, "VirtualIOServer", include_schema_version=False)
        if not vios_xml:
            raise HMCError(f"GET {get_path} returned empty response", 200, "")

        # Step 2 — Parse raw XML and locate VirtualSCSIMappings element
        try:
            root = ET.fromstring(vios_xml)
        except ET.ParseError as exc:
            raise HMCError(
                "VirtualIOServer GET response is not valid XML", 200, vios_xml
            ) from exc

        vios_elem = root.find(f".//{{{_UOM_NS}}}VirtualIOServer")
        if vios_elem is None:
            vios_elem = root  # already the VirtualIOServer element
        sys_uuid = _extract_system_uuid_from_vios_xml(vios_xml)

        mappings_elem = vios_elem.find(f"{{{_UOM_NS}}}VirtualSCSIMappings")
        if mappings_elem is None:
            raise HMCError(
                "VirtualIOServer document has no VirtualSCSIMappings element; "
                "the VIOS may not be configured for vSCSI",
                200,
                vios_xml,
            )

        # Step 4 — Build the new mapping XML element and append it
        lpar_link = (
            f"{self._rest_base_url}/rest/api/uom/ManagedSystem/{sys_uuid}"
            f"/LogicalPartition/{lpar_uuid}"
        )
        mount_type = "r"  # read-only optical media
        new_mapping_xml = (
            f'<VirtualSCSIMapping xmlns="{_UOM_NS}" xmlns:atom="{_ATOM_NS}" schemaVersion="V1_0">'
            f"<Metadata><Atom/></Metadata>"
            f'<AssociatedLogicalPartition kb="CUR" kxe="false"'
            f' href="{lpar_link}" rel="related"/>'
            f'<Storage kxe="false" kb="CUR">'
            f'<VirtualOpticalMedia schemaVersion="V1_0">'
            f"<Metadata><Atom/></Metadata>"
            f'<MediaName kxe="false" kb="CUR">{media_name}</MediaName>'
            f'<MountType kxe="false" kb="CUD">{mount_type}</MountType>'
            f"</VirtualOpticalMedia>"
            f"</Storage>"
            f"</VirtualSCSIMapping>"
        )
        try:
            new_mapping_elem = ET.fromstring(new_mapping_xml)
        except ET.ParseError as exc:
            raise HMCError(
                "Failed to build VirtualSCSIMapping XML", 0, new_mapping_xml
            ) from exc
        mappings_elem.append(new_mapping_elem)

        # Step 5 — POST the full modified VIOS document to the system-scoped endpoint
        post_path = f"/rest/api/uom/ManagedSystem/{sys_uuid}/VirtualIOServer/{vios_uuid}"
        body = ET.tostring(vios_elem, encoding="unicode")
        # Use Accept: */* as the reference implementation does; Content-Type stays VirtualIOServer
        resp = await self._request(
            "POST",
            post_path,
            content=body,
            headers={
                "Accept": "*/*",
                "Content-Type": "application/vnd.ibm.powervm.uom+xml; type=VirtualIOServer",
            },
        )
        if resp.status_code not in (200, 201, 202):
            raise HMCError(
                f"POST {post_path} failed", resp.status_code, resp.text
            )
        entries = _parse_feed(resp.text, post_path) if resp.text else []
        return entries[0].get("Resource", entries[0]) if entries else None

    async def delete_optical_mapping(
        self: StorageClient,
        vios_uuid: str,
        lpar_uuid: str,
        media_name: str,
    ) -> None:
        """Remove the VirtualSCSIMapping for an optical device via read-modify-write.

        VirtualSCSIMapping elements have no UUID-addressable sub-resource on this
        HMC firmware, so the standard DELETE /VirtualSCSIMapping/{uuid} path does not
        work.  Instead this method:
          1. GETs the full VirtualIOServer document.
          2. Finds the mapping whose AssociatedLogicalPartition href contains
             lpar_uuid AND whose Storage/VirtualOpticalMedia/MediaName matches
             media_name.
          3. Removes that element and POSTs the modified document back.

        The backing VirtualOpticalMedia (ISO container) is preserved.
        """
        ET.register_namespace("", _UOM_NS)
        ET.register_namespace("atom", _ATOM_NS)

        get_path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}"
        vios_xml = await self._get(get_path, "VirtualIOServer", include_schema_version=False)
        if not vios_xml:
            raise HMCError(f"GET {get_path} returned empty response", 200, "")

        try:
            root = ET.fromstring(vios_xml)
        except ET.ParseError as exc:
            raise HMCError(
                "VirtualIOServer GET response is not valid XML", 200, vios_xml
            ) from exc

        vios_elem = root.find(f".//{{{_UOM_NS}}}VirtualIOServer")
        if vios_elem is None:
            vios_elem = root
        sys_uuid = _extract_system_uuid_from_vios_xml(vios_xml)

        mappings_elem = vios_elem.find(f"{{{_UOM_NS}}}VirtualSCSIMappings")
        if mappings_elem is None:
            return  # No mappings — nothing to remove.

        to_remove = None
        for mapping in list(mappings_elem):
            xml_str = ET.tostring(mapping, encoding="unicode")
            if lpar_uuid in xml_str and media_name in xml_str:
                to_remove = mapping
                break

        if to_remove is None:
            return  # Mapping already gone — idempotent.

        mappings_elem.remove(to_remove)

        post_path = f"/rest/api/uom/ManagedSystem/{sys_uuid}/VirtualIOServer/{vios_uuid}"
        body = ET.tostring(vios_elem, encoding="unicode")
        resp = await self._request(
            "POST",
            post_path,
            content=body,
            headers={
                "Accept": "*/*",
                "Content-Type": "application/vnd.ibm.powervm.uom+xml; type=VirtualIOServer",
            },
        )
        if resp.status_code not in (200, 201, 202):
            raise HMCError(
                f"POST {post_path} failed", resp.status_code, resp.text
            )
