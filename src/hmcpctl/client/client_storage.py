"""HMCClient storage mixin.

The full client is assembled in :mod:`hmcpctl.client` by inheriting every
domain mixin; this module only defines methods for storage.
"""

from __future__ import annotations

import re as _re

# ElementTree is retained for element construction, traversal, typing, and
# serialization only. Every inbound HMC response is parsed with defusedxml.
import xml.etree.ElementTree as ET  # nosec B405
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

from defusedxml import ElementTree as DET

from ..documents import (
    StorageKind,
    build_brokered_file_document,
    build_linked_optical_media_document,
    build_virtual_disk_element,
    build_virtual_optical_mapping_document,
    build_volume_group_document,
    build_vscsi_mapping_document,
)
from ..errors import HMCError
from ..xmlutil import element_to_dict, localname
from .client_contracts import StorageClient, _reject_non_uuid_path_argument
from .client_parse import _parse_feed

# HMC UOM namespace — used in read-modify-write VolumeGroup operations.
_UOM_NS = "http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/"
_ATOM_NS = "http://www.w3.org/2005/Atom"
_MEDIA_UOM = "application/vnd.ibm.powervm.uom+xml"
_UUID_PATTERN = _re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\Z")


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


def _whole_gib(size_mib: int) -> int:
    """Convert a MiB size to the whole GiB that RepositorySize and media Size take.

    The HMC reads both fields as GiB. A size that is not a whole number of GiB
    is refused rather than sent as a fraction whose precision the HMC has not
    been observed to accept.
    """
    if size_mib <= 0 or size_mib % 1024:
        raise ValueError(
            f"size_mib must be a positive multiple of 1024 (whole GiB); got {size_mib}"
        )
    return size_mib // 1024


def _same_gib(stored: str | None, size_gib: int) -> bool:
    """Whether an HMC GiB text such as "7" or "7.0" equals ``size_gib``."""
    try:
        return stored is not None and Decimal(stored) == size_gib
    except InvalidOperation:
        return False


def _extract_optical_media(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return media entries from documented and legacy repository shapes."""
    optical_media: list[dict[str, Any]] = []
    for entry in entries:
        resource = entry.get("Resource")
        if not isinstance(resource, dict):
            continue
        repositories = resource.get("MediaRepositories") or resource
        if not isinstance(repositories, dict):
            continue
        repository = repositories.get("VirtualMediaRepository")
        if not isinstance(repository, dict):
            continue
        media_container = repository.get("OpticalMedia") or repository
        if not isinstance(media_container, dict):
            continue
        media = media_container.get("VirtualOpticalMedia", [])
        if isinstance(media, list):
            optical_media.extend(item for item in media if isinstance(item, dict))
        elif isinstance(media, dict):
            optical_media.append(media)
    return optical_media


def lpar_uuid_from_href(href: object) -> str | None:
    """Return the LPAR UUID ending an HMC ``.../LogicalPartition/<uuid>`` link, or None.

    The HMC links a mapping's client LPAR absolutely and system-scoped
    (``https://<hmc>/rest/api/uom/ManagedSystem/<sys>/LogicalPartition/<uuid>``), so
    only the final path segment after the marker identifies the partition (ADR 0168).
    """
    if not isinstance(href, str):
        return None
    _, marker, tail = urlparse(href).path.rpartition("/LogicalPartition/")
    return tail if marker and tail and "/" not in tail else None


def _device_name(value: object) -> str | None:
    # element_to_dict yields {"@attrs": ..., "text": ...} for a leaf carrying
    # attributes it does not ignore; the name is its text either way.
    text = value.get("text") if isinstance(value, Mapping) else value
    return text if isinstance(text, str) and text and "/" not in text else None


def storage_mapping_id(mapping: Mapping[str, Any]) -> str | None:
    """Return a VirtualSCSIMapping's ``<server adapter>/<target device>`` identity.

    The HMC sends no mapping UUID; the VIOS device names ``vhost0/vtscsi0`` identify
    it (ADR 0168). Returns None unless both names exist and ``TargetDevice`` holds
    exactly one device element.
    """
    adapter = mapping.get("ServerAdapter")
    target = mapping.get("TargetDevice")
    devices = (
        [value for key, value in target.items() if not key.startswith("@")]
        if isinstance(target, Mapping)
        else []
    )
    if (
        not isinstance(adapter, Mapping)
        or len(devices) != 1
        or not isinstance(devices[0], Mapping)
    ):
        return None
    adapter_name = _device_name(adapter.get("AdapterName"))
    target_name = _device_name(devices[0].get("TargetName"))
    return f"{adapter_name}/{target_name}" if adapter_name and target_name else None


def _filter_optical_mappings(
    mappings: list[dict[str, Any]], lpar_uuid: str | None
) -> list[dict[str, Any]]:
    """Keep optical-backed mappings, optionally scoped to one client LPAR."""
    optical = [
        mapping
        for mapping in mappings
        if isinstance(mapping.get("Storage"), dict)
        and "VirtualOpticalMedia" in mapping["Storage"]
    ]
    if lpar_uuid is None:
        return optical
    return [mapping for mapping in optical if _mapping_targets_lpar(mapping, lpar_uuid)]


def mapping_lpar_uuid(mapping: Mapping[str, Any]) -> str | None:
    """Return the client-LPAR UUID a parsed VirtualSCSIMapping links to, or None."""
    partition = mapping.get("AssociatedLogicalPartition")
    return lpar_uuid_from_href(
        partition.get("href") if isinstance(partition, Mapping) else None
    )


def _mapping_targets_lpar(mapping: Mapping[str, Any], lpar_uuid: str) -> bool:
    return mapping_lpar_uuid(mapping) == lpar_uuid


def _children_named(parent: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in parent if localname(child.tag) == name]


def _required_etag(etag: str | None) -> str:
    """The GET's ETag; a VolumeGroup write never falls back to an unconditional POST."""
    if not etag:
        raise HMCError(
            "VolumeGroup GET returned no ETag; refusing a whole-group write "
            "that could overwrite a concurrent change. Retry, and report the HMC version "
            "if it persists."
        )
    return etag


def _virtual_disks(vg_elem: ET.Element) -> ET.Element:
    """The group's VirtualDisks collection, appended (last in the XSD) when absent."""
    found = _children_named(vg_elem, "VirtualDisks")
    if found:
        return found[0]
    disks = ET.SubElement(
        vg_elem,
        f"{{{_UOM_NS}}}VirtualDisks",
        attrib={"kb": "CUD", "kxe": "false", "schemaVersion": "V1_0"},
    )
    ET.SubElement(ET.SubElement(disks, f"{{{_UOM_NS}}}Metadata"), f"{{{_UOM_NS}}}Atom")
    return disks


def _disks_named(disks: ET.Element, disk_name: str) -> list[ET.Element]:
    return [
        disk
        for disk in _children_named(disks, "VirtualDisk")
        if any(name.text == disk_name for name in _children_named(disk, "DiskName"))
    ]


async def _append_vios_mapping(
    client: StorageClient,
    operation: str,
    path: str,
    uuid_path_arguments: Mapping[str, str],
    mapping_document: str,
) -> str:
    """Add one mapping by read-modify-write of the VIOS ``ViosSCSIMapping`` group.

    The fetched mappings are posted back unchanged beside the new one, under the
    GET's ETag, so the create never replaces the VIOS's mapping set (ADR 0169).
    """
    vios_uuid = uuid_path_arguments["vios_uuid"]
    got = await client._request_with_uuid_path_arguments(
        "GET",
        path,
        uuid_path_arguments=uuid_path_arguments,
        headers={"Accept": f"{_MEDIA_UOM}; type=VirtualIOServer"},
    )
    if got.status_code != 200:
        raise HMCError(f"GET {path} failed", got.status_code, got.text)
    etag = got.headers.get("ETag")
    if not etag:
        raise HMCError(
            f"GET {path} returned no ETag; refusing {operation} without If-Match",
            200,
            got.text[:500],
        )
    ET.register_namespace("", _UOM_NS)
    ET.register_namespace("atom", _ATOM_NS)
    try:
        vios_elem = _find_vios_element(DET.fromstring(got.text), vios_uuid)
    except DET.ParseError as exc:
        raise HMCError(f"GET {path} response is not valid XML", 200, got.text) from exc
    mappings = vios_elem.find(f"{{{_UOM_NS}}}VirtualSCSIMappings")
    if mappings is None:
        raise HMCError(
            f"GET {path} returned no VirtualSCSIMappings; refusing {operation} "
            "because the post could replace the VIOS mapping set. If the VIOS has "
            "no mappings yet, create the first one on the VIOS (mkvdev) or in the "
            "HMC GUI, then retry",
            200,
            got.text[:500],
        )
    mappings.append(
        DET.fromstring(mapping_document).find(f".//{{{_UOM_NS}}}VirtualSCSIMapping")
    )

    async def dispatch() -> str:
        response = await client._request_with_uuid_path_arguments(
            "POST",
            path,
            uuid_path_arguments=uuid_path_arguments,
            content=ET.tostring(vios_elem, encoding="unicode"),
            headers={
                "Accept": "*/*",
                "Content-Type": f"{_MEDIA_UOM}; type=VirtualIOServer",
                "If-Match": etag,
            },
        )
        if response.status_code == 412:
            raise HMCError(
                f"{operation}: VIOS {vios_uuid} mappings changed since they were "
                "read; nothing was written, re-run to retry",
                412,
                response.text,
            )
        if response.status_code not in (200, 201, 202):
            raise HMCError(f"POST {path} failed", response.status_code, response.text)
        return response.text

    return await client._reconcile_storage_mutation(
        operation, lambda: client.list_storage_mappings(vios_uuid), dispatch
    )


class StorageMixin:
    async def _broker_file_create(
        self: StorageClient, vios_uuid: str, vg_uuid: str, filename: str
    ) -> str:
        """Create the storage broker handle used to import an ISO."""
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}"
        response = await self._request_with_uuid_path_arguments(
            "POST",
            path,
            uuid_path_arguments={"vios_uuid": vios_uuid, "vg_uuid": vg_uuid},
            content=build_brokered_file_document(filename=filename),
            headers={"Content-Type": _MEDIA_UOM, "Accept": _MEDIA_UOM},
        )
        if response.status_code not in (200, 201):
            raise HMCError(
                f"Brokered file create failed for {filename}",
                response.status_code,
                response.text,
            )
        location = response.headers.get("Location")
        if not location:
            raise HMCError(
                "Brokered file create missing Location header",
                response.status_code,
                response.text,
            )
        return location

    async def _broker_file_upload(
        self: StorageClient,
        broker_uri: str,
        content: AsyncIterator[bytes],
        content_length: int,
    ) -> str:
        """Stream bounded ISO content into a storage broker handle."""
        response = await self._request(
            "PUT",
            broker_uri,
            content=content,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(content_length),
                "Accept": _MEDIA_UOM,
            },
        )
        if response.status_code not in (200, 201, 202):
            raise HMCError(
                f"Brokered file upload failed to {broker_uri}",
                response.status_code,
                response.text,
            )
        return response.text or ""

    async def _broker_iso_import(
        self: StorageClient,
        vios_uuid: str,
        vg_uuid: str,
        media_name: str,
        broker_uri: str,
    ) -> str:
        """Import a brokered ISO into a VIOS media repository."""
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}"
        document = build_linked_optical_media_document(
            media_name=media_name, broker_uri=broker_uri
        )
        response = await self._reconcile_storage_mutation(
            "broker_iso_import",
            lambda: self.get_volume_group(vios_uuid, vg_uuid),
            lambda: self._post(
                path,
                document,
                resource_type="VolumeGroup",
                uuid_path_arguments={"vios_uuid": vios_uuid, "vg_uuid": vg_uuid},
                fallback_to_generic_uom_on_406=True,
            ),
        )
        return response or ""

    async def _broker_file_cleanup(self: StorageClient, broker_uri: str) -> None:
        """Release a storage broker handle after an ISO upload."""
        response = await self._request(
            "DELETE", broker_uri, headers={"Accept": _MEDIA_UOM}
        )
        if response.status_code not in (200, 202, 204, 404):
            raise HMCError(
                f"Brokered file cleanup failed for {broker_uri}",
                response.status_code,
                response.text,
            )

    # Virtual storage (children of VirtualIOServer)
    def get_lpar_link(self: StorageClient, lpar_uuid: str) -> str:
        """Atom SELF href for an LPAR (used when building mappings)."""
        _reject_non_uuid_path_argument("lpar_uuid", lpar_uuid)
        return f"{self._rest_base_url}/rest/api/uom/LogicalPartition/{lpar_uuid}"

    async def _reconcile_storage_mutation(
        self: StorageClient,
        operation: str,
        snapshot: Callable[[], Awaitable[Any]],
        dispatch: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Read state around a failed storage mutation without retrying it."""
        try:
            return await dispatch()
        except HMCError as exc:
            if exc.status_code is None or not 500 <= exc.status_code <= 599:
                raise
            try:
                await snapshot()
            except HMCError as readback_error:
                observation = f"readback failed: {readback_error}"
            else:
                observation = "readback completed"
            raise HMCError(
                f"{operation} may have a possible side effect. Do not retry until state "
                f"is verified; {observation}",
                exc.status_code,
                exc.body,
            ) from exc

    async def list_volume_groups(
        self: StorageClient, vios_uuid: str
    ) -> list[dict[str, Any]]:
        """List Volume Groups on a VIOS (free space, PVs, virtual disks).

        The VolumeGroup endpoint returns HTTP 204 when X-HMC-Schema-Version is
        present, so we deliberately omit the schema-version header here.
        """
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup"
        xml = await self._get(
            path,
            "VolumeGroup",
            include_schema_version=False,
            uuid_path_arguments={"vios_uuid": vios_uuid},
        )
        return _parse_feed(xml, path) if xml else []

    async def get_volume_group(
        self: StorageClient, vios_uuid: str, vg_uuid: str
    ) -> dict[str, Any] | None:
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}"
        xml = await self._get(
            path,
            "VolumeGroup",
            include_schema_version=False,
            uuid_path_arguments={"vios_uuid": vios_uuid, "vg_uuid": vg_uuid},
        )
        if not xml:
            return None
        entries = _parse_feed(xml, path)
        return entries[0] if entries else None

    async def create_volume_group(
        self: StorageClient,
        vios_uuid: str,
        name: str,
        physical_volumes: list[str],
    ) -> dict[str, Any] | None:
        """Create a Volume Group on a VIOS from physical volumes (e.g. ['hdisk10'])."""

        xml = build_volume_group_document(name, physical_volumes)
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup"
        resp = await self._reconcile_storage_mutation(
            "create_volume_group",
            lambda: self.list_volume_groups(vios_uuid),
            lambda: self._put(
                path,
                xml,
                resource_type="VolumeGroup",
                include_schema_version=False,
                uuid_path_arguments={"vios_uuid": vios_uuid},
                fallback_to_generic_uom_on_406=True,
            ),
        )
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

        Read-modify-write (#936): GET the whole VolumeGroup, insert the new
        VirtualDisk after the VirtualDisks Metadata, and POST the whole element
        back with If-Match set to the GET's ETag, so existing disks and physical
        volumes are carried through unchanged. Refuses, without writing, a GET
        with no ETag and a name the group already holds.
        """

        for argument, value in (("vios_uuid", vios_uuid), ("vg_uuid", vg_uuid)):
            if not _UUID_PATTERN.fullmatch(value):
                raise ValueError(f"{argument} must be a UUID")
        element = DET.fromstring(build_virtual_disk_element(disk_name, capacity_mib))
        etag, vg_elem = await self._get_vg_raw_xml(vios_uuid, vg_uuid)
        etag = _required_etag(etag)
        disks = _virtual_disks(vg_elem)
        if _disks_named(disks, disk_name):
            raise HMCError(
                f"Virtual disk {disk_name!r} already exists in the volume group; "
                "create does not replace it.",
                409,
            )
        metadata = _children_named(disks, "Metadata")
        disks.insert(list(disks).index(metadata[0]) + 1 if metadata else 0, element)
        return await self._post_vg_xml(
            vios_uuid, vg_uuid, vg_elem, operation="create_virtual_disk", etag=etag
        )

    async def delete_virtual_disk(
        self: StorageClient, vios_uuid: str, vg_uuid: str, disk_name: str
    ) -> dict[str, Any] | None:
        """Delete a Virtual Disk (logical volume) from a Volume Group.

        Read-modify-write (#936): GET the whole VolumeGroup, remove the one
        VirtualDisk whose DiskName matches, and POST the whole element back with
        If-Match set to the GET's ETag. Refuses, without writing, a GET with no
        ETag and zero or several matching disks.
        """

        etag, vg_elem = await self._get_vg_raw_xml(vios_uuid, vg_uuid)
        etag = _required_etag(etag)
        collections = _children_named(vg_elem, "VirtualDisks")
        matches = _disks_named(collections[0], disk_name) if collections else []
        if len(matches) != 1:
            raise HMCError(
                f"Refusing to delete virtual disk {disk_name!r}: the volume group holds "
                f"{len(matches)} virtual disks with that name; expected exactly one.",
                409 if matches else 404,
            )
        collections[0].remove(matches[0])
        return await self._post_vg_xml(
            vios_uuid, vg_uuid, vg_elem, operation="delete_virtual_disk", etag=etag
        )

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
        The HMC creates the client/server adapter pair for the mapping, and the
        VIOS's existing mappings are preserved (ADR 0169).
        """

        lpar_link = self.get_lpar_link(lpar_uuid)
        xml = build_vscsi_mapping_document(
            storage_kind, storage_name, lpar_link, target_device=target_device
        )
        _reject_non_uuid_path_argument("vios_uuid", vios_uuid)
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}?group=ViosSCSIMapping"
        resp = await _append_vios_mapping(
            self, "map_storage_to_lpar", path, {"vios_uuid": vios_uuid}, xml
        )
        entries = _parse_feed(resp, path) if resp else []
        return entries[0] if entries else None

    # Storage Mapping Inventory and Detach
    async def list_storage_mappings(
        self: StorageClient, vios_uuid: str, lpar_uuid: str | None = None
    ) -> list[dict[str, Any]]:
        """List VirtualSCSIMappings on a VIOS, optionally filtered by LPAR.

        Returns mappings with backing storage details (PhysicalVolume or VirtualDisk)
        and client LPAR information. Use lpar_uuid to scope mappings to a single LPAR.

        Requests the documented ``ViosSCSIMapping`` extended group.
        """
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}?group=ViosSCSIMapping"
        xml = await self._get(
            path,
            "VirtualIOServer",
            uuid_path_arguments={"vios_uuid": vios_uuid},
        )
        if not xml:
            return []

        entries = _parse_feed(xml, path)
        if not entries:
            return []

        detail = entries[0]
        mappings = detail.get("Resource", {}).get("VirtualSCSIMappings", {})
        if not isinstance(mappings, dict):
            return []
        mappings = mappings.get("VirtualSCSIMapping", [])
        if not isinstance(mappings, list):
            mappings = [mappings] if mappings else []

        if lpar_uuid:
            mappings = [
                m
                for m in mappings
                if isinstance(m, dict) and _mapping_targets_lpar(m, lpar_uuid)
            ]

        return mappings if isinstance(mappings, list) else [mappings]

    async def delete_storage_mapping(
        self: StorageClient, vios_uuid: str, mapping_id: str, lpar_uuid: str
    ) -> None:
        """Detach one mapping through its parent VirtualIOServer document.

        ``mapping_id`` is the ``<server adapter>/<target device>`` identity from
        :func:`storage_mapping_id`. Exactly one mapping in the fetched document must
        carry it, and its client-LPAR link must name ``lpar_uuid``, the partition the
        caller authorized; otherwise nothing is posted (ADR 0168).
        """
        if not mapping_id:
            raise ValueError("Storage mapping ID must not be empty")
        ET.register_namespace("", _UOM_NS)
        ET.register_namespace("atom", _ATOM_NS)

        get_path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}"
        vios_xml = await self._get(
            get_path,
            "VirtualIOServer",
            include_schema_version=False,
            uuid_path_arguments={"vios_uuid": vios_uuid},
        )
        if not vios_xml:
            raise HMCError(f"GET {get_path} returned empty response", 200, "")
        try:
            root = DET.fromstring(vios_xml)
        except DET.ParseError as exc:
            raise HMCError(
                "VirtualIOServer GET response is not valid XML", 200, vios_xml
            ) from exc

        vios_elem = _find_vios_element(root, vios_uuid)
        mappings = vios_elem.find(f"{{{_UOM_NS}}}VirtualSCSIMappings")
        not_found = f"Storage mapping {mapping_id!r} not found on VIOS {vios_uuid!r}"
        if mappings is None:
            raise HMCError(not_found)
        matches = [
            (mapping, parsed)
            for mapping in mappings.findall(f"{{{_UOM_NS}}}VirtualSCSIMapping")
            if isinstance(parsed := element_to_dict(mapping), dict)
            and storage_mapping_id(parsed) == mapping_id
        ]
        if not matches:
            raise HMCError(not_found)
        if len(matches) > 1:
            raise HMCError(
                f"VirtualSCSIMapping {mapping_id!r} is duplicated; "
                "refusing an ambiguous detach"
            )
        target, parsed = matches[0]
        if not _mapping_targets_lpar(parsed, lpar_uuid):
            raise HMCError(
                f"Storage mapping {mapping_id!r} does not belong to LPAR {lpar_uuid!r}; "
                "refusing to detach a mapping that was not authorized"
            )

        mappings.remove(target)
        system_uuid = _extract_system_uuid_from_vios(vios_elem)
        post_path = (
            f"/rest/api/uom/ManagedSystem/{system_uuid}/VirtualIOServer/{vios_uuid}"
        )
        async def dispatch() -> None:
            response = await self._request_with_uuid_path_arguments(
                "POST",
                post_path,
                uuid_path_arguments={
                    "system_uuid": system_uuid,
                    "vios_uuid": vios_uuid,
                },
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

        await self._reconcile_storage_mutation(
            "delete_storage_mapping",
            lambda: self._get(
                get_path,
                "VirtualIOServer",
                include_schema_version=False,
                uuid_path_arguments={"vios_uuid": vios_uuid},
            ),
            dispatch,
        )

    # Virtual media repository (VolumeGroup read-modify-write operations)

    async def _get_vg_raw_xml(
        self: StorageClient, vios_uuid: str, vg_uuid: str
    ) -> tuple[str | None, ET.Element]:
        """GET the full VolumeGroup XML and return (ETag, VolumeGroup element).

        Parses the Atom feed to extract the single VolumeGroup element.
        The returned ET.Element is a copy with namespace prefixes re-registered
        so subsequent serialisation round-trips cleanly.
        """
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}"
        resp = await self._request_with_uuid_path_arguments(
            "GET",
            path,
            uuid_path_arguments={"vios_uuid": vios_uuid, "vg_uuid": vg_uuid},
            headers=self._uom_headers("VolumeGroup", include_schema_version=False),
        )
        if resp.status_code not in (200, 204):
            raise HMCError(f"GET {path} failed", resp.status_code, resp.text)
        raw = resp.text if resp.status_code == 200 else ""
        if not raw:
            raise HMCError(f"GET {path} returned empty body", 200, "")

        # The HMC rejects auto-generated ns0/ns1 prefixes despite equivalent URIs.
        ET.register_namespace("", _UOM_NS)
        ET.register_namespace("atom", _ATOM_NS)

        root = DET.fromstring(raw)
        # Firmware returns either an Atom-wrapped or bare VolumeGroup document. Only
        # the root or an Atom content child is the group: each VirtualDisk carries a
        # nested VolumeGroup link element that an unanchored search would select.
        ns = {"atom": _ATOM_NS, "uom": _UOM_NS}
        if localname(root.tag) == "VolumeGroup":
            vg_elem = root
        else:
            vg_elem = root.find(".//atom:content/uom:VolumeGroup", ns)
            if vg_elem is None:
                vg_elem = root.find(".//atom:content/VolumeGroup", ns)
        if vg_elem is None:
            raise HMCError(
                f"GET {path} response contains no VolumeGroup element",
                200,
                raw[:500],
            )
        return resp.headers.get("ETag"), vg_elem

    async def _post_vg_xml(
        self: StorageClient,
        vios_uuid: str,
        vg_uuid: str,
        vg_elem: ET.Element,
        *,
        operation: str = "update_virtual_media_repository",
        etag: str | None = None,
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
        if etag:
            headers["If-Match"] = etag
        body = ET.tostring(vg_elem, encoding="unicode", xml_declaration=False)
        async def dispatch() -> Any:
            resp = await self._request_with_uuid_path_arguments(
                "POST",
                path,
                uuid_path_arguments={"vios_uuid": vios_uuid, "vg_uuid": vg_uuid},
                content=body,
                headers=headers,
            )
            if resp.status_code == 412:
                raise HMCError(
                    f"POST {path} refused: the volume group changed since it was read "
                    "(If-Match mismatch). Nothing was written; re-run the operation.",
                    412,
                    resp.text,
                )
            if resp.status_code not in (200, 201, 202):
                raise HMCError(f"POST {path} failed", resp.status_code, resp.text)
            return resp

        resp = await self._reconcile_storage_mutation(
            operation,
            lambda: self._get(
                path,
                "VolumeGroup",
                include_schema_version=False,
                uuid_path_arguments={"vios_uuid": vios_uuid, "vg_uuid": vg_uuid},
            ),
            dispatch,
        )
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
        return vg_elem.find(f".//{{{_UOM_NS}}}VirtualMediaRepository") or vg_elem.find(
            ".//VirtualMediaRepository"
        )

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
        size_el.text = str(_whole_gib(size_mib))
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

        insert_at = next(
            (
                i
                for i, child in enumerate(children)
                if child.tag == pvs_tag or _local(child.tag) == "PhysicalVolumes"
            ),
            None,
        )
        if insert_at is None:
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
        then POST the modified XML back with If-Match set to the GET's ETag (ADR 0171).
        This is the only approach that works on HMC V10R3 firmware (minimal-payload
        POSTs return HTTP 406 or 500).

        ``size_mib`` is MiB; the HMC's RepositorySize is GiB, so it is sent as
        ``size_mib / 1024`` and must be a whole number of GiB.
        """
        size_gib = _whole_gib(size_mib)
        etag, vg_elem = await self._get_vg_raw_xml(vios_uuid, vg_uuid)

        existing = self._find_vmlib(vg_elem)
        if existing is not None:
            name = existing.findtext(
                f"{{{_UOM_NS}}}RepositoryName"
            ) or existing.findtext("RepositoryName")
            size = existing.findtext(
                f"{{{_UOM_NS}}}RepositorySize"
            ) or existing.findtext("RepositorySize")
            if _same_gib(size, size_gib):
                return {
                    "Resource": {
                        "RepositoryName": name or "VMLibrary",
                        "RepositorySize": size,
                    }
                }
            observed = f"{size} GiB" if size else "an unknown size"
            raise HMCError(
                "Virtual media repository already exists with size "
                f"{observed}; requested {size_mib} MiB ({size_gib} GiB). "
                "Create does not replace or resize an existing repository; "
                "use an explicitly destructive repository operation.",
                409,
            )

        mr = self._build_mr_element(size_mib)
        self._insert_mr_at_correct_position(vg_elem, mr)

        return await self._post_vg_xml(
            vios_uuid, vg_uuid, vg_elem, etag=_required_etag(etag)
        )

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
        VirtualMediaRepository, then POST the modified XML back with If-Match set to
        the GET's ETag (ADR 0171).

        The HMC XSD structure inside VirtualMediaRepository is:
          Metadata, OpticalMedia (container for VirtualOpticalMedia entries),
          RepositoryName, RepositorySize.

        ``size_mib`` is MiB; the medium's Size is GiB, so it is sent as
        ``size_mib / 1024`` and must be a whole number of GiB.
        """
        size_gib = _whole_gib(size_mib)
        etag, vg_elem = await self._get_vg_raw_xml(vios_uuid, vg_uuid)

        vmlib = self._find_vmlib(vg_elem)
        if vmlib is None:
            raise HMCError(
                "No VirtualMediaRepository found in VolumeGroup — "
                "call create_media_repository first",
                404,
                "",
            )

        opt_media_tag = f"{{{_UOM_NS}}}OpticalMedia"
        opt_media = vmlib.find(opt_media_tag) or vmlib.find(".//OpticalMedia")
        if opt_media is None:
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
            opt_media = ET.Element(opt_media_tag, attrib={"schemaVersion": "V1_0"})
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
        # The HMC XSD names this field Size, not MediaSize, and measures it in GiB.
        s = ET.SubElement(vom, f"{{{_UOM_NS}}}Size")
        s.text = str(size_gib)
        t = ET.SubElement(vom, f"{{{_UOM_NS}}}MountType")
        t.text = "rw"

        return await self._post_vg_xml(
            vios_uuid, vg_uuid, vg_elem, etag=_required_etag(etag)
        )

    async def delete_media_repository(
        self: StorageClient, vios_uuid: str, vg_uuid: str
    ) -> dict[str, Any] | None:
        """Delete the Virtual Media Repository (VMLibrary) from a Volume Group.

        Uses a read-modify-write pattern: GET the full VolumeGroup XML, remove the
        MediaRepositories block, then POST the modified XML back with If-Match set to
        the GET's ETag (ADR 0171).
        """
        etag, vg_elem = await self._get_vg_raw_xml(vios_uuid, vg_uuid)

        mr_tag = f"{{{_UOM_NS}}}MediaRepositories"
        mr = vg_elem.find(f".//{mr_tag}")
        if mr is None:
            return None
        vg_elem.remove(mr)

        return await self._post_vg_xml(
            vios_uuid, vg_uuid, vg_elem, etag=_required_etag(etag)
        )

    async def delete_optical_media(
        self: StorageClient, vios_uuid: str, vg_uuid: str, media_name: str
    ) -> dict[str, Any] | None:
        """Delete a VirtualOpticalMedia (ISO image) from the media repository.

        Uses a read-modify-write pattern: GET the full VolumeGroup XML, remove the
        named VirtualOpticalMedia node from the OpticalMedia container, then POST back
        with If-Match set to the GET's ETag (ADR 0171).
        """
        etag, vg_elem = await self._get_vg_raw_xml(vios_uuid, vg_uuid)

        vmlib = self._find_vmlib(vg_elem)
        if vmlib is None:
            return None  # Nothing to remove.

        opt_media_tag = f"{{{_UOM_NS}}}OpticalMedia"
        vom_tag = f"{{{_UOM_NS}}}VirtualOpticalMedia"
        name_tag = f"{{{_UOM_NS}}}MediaName"

        # Older firmware may place media directly beneath the repository.
        opt_media = vmlib.find(opt_media_tag) or vmlib.find(".//OpticalMedia")
        search_in = opt_media if opt_media is not None else vmlib

        to_remove: ET.Element | None = None
        for vom in list(search_in.findall(vom_tag)):
            n = vom.find(name_tag)
            if n is not None and n.text == media_name:
                to_remove = vom
                break
        if to_remove is None:
            return None
        search_in.remove(to_remove)

        return await self._post_vg_xml(
            vios_uuid, vg_uuid, vg_elem, etag=_required_etag(etag)
        )

    async def get_media_repository(
        self: StorageClient, vios_uuid: str, vg_uuid: str
    ) -> dict[str, Any] | None:
        """Get the Virtual Media Repository (VMLibrary) from a Volume Group.

        Returns the repository with capacity (RepositorySize, in GiB) and optionally
        embedded VirtualOpticalMedia entries if present. Returns None if the
        Volume Group does not exist or has no media repository.
        """
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}"
        try:
            xml = await self._get(
                path,
                "VolumeGroup",
                include_schema_version=False,
                uuid_path_arguments={"vios_uuid": vios_uuid, "vg_uuid": vg_uuid},
            )
        except HMCError as exc:
            if exc.status_code == 404:
                return None
            raise
        if not xml:
            return None
        entries = _parse_feed(xml, path)
        if not entries:
            return None
        entry = entries[0]
        resource = entry.get("Resource")
        if not isinstance(resource, dict):
            return None
        repositories = resource.get("MediaRepositories") or resource
        if not isinstance(repositories, dict):
            return None
        if "VirtualMediaRepository" not in repositories:
            return None
        return entry

    async def list_optical_media(
        self: StorageClient, vios_uuid: str, vg_uuid: str
    ) -> list[dict[str, Any]]:
        """List Virtual Optical Media in the Virtual Media Repository.

        Returns a list of optical media entries (ISO containers) with their
        MediaName, Size (GiB), and MediaType. Returns empty list if the
        Volume Group does not exist or has no media repository.
        """
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}"
        try:
            xml = await self._get(
                path,
                "VolumeGroup",
                include_schema_version=False,
                uuid_path_arguments={"vios_uuid": vios_uuid, "vg_uuid": vg_uuid},
            )
        except HMCError as exc:
            if exc.status_code == 404:
                return []
            raise
        if not xml:
            return []

        entries = _parse_feed(xml, path)
        if not entries:
            return []

        return _extract_optical_media(entries)

    # Virtual Optical Mapping (VirtualSCSIMapping for VirtualOpticalMedia)
    async def list_optical_mappings(
        self: StorageClient, vios_uuid: str, lpar_uuid: str | None = None
    ) -> list[dict[str, Any]]:
        """List VirtualSCSIMappings for optical media on a VIOS, optionally filtered by LPAR.

        Returns only mappings that reference VirtualOpticalMedia backing, with media
        details and client LPAR information. Use lpar_uuid to scope mappings to a single LPAR.
        """
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}?group=ViosSCSIMapping"
        xml = await self._get(
            path,
            "VirtualIOServer",
            uuid_path_arguments={"vios_uuid": vios_uuid},
        )
        if not xml:
            return []

        entries = _parse_feed(xml, path)
        if not entries:
            return []

        detail = entries[0]
        mappings = detail.get("Resource", {}).get("VirtualSCSIMappings", {})
        if not isinstance(mappings, dict):
            return []
        mappings = mappings.get("VirtualSCSIMapping", [])
        if not isinstance(mappings, list):
            mappings = [mappings] if mappings else []

        return _filter_optical_mappings(
            [mapping for mapping in mappings if isinstance(mapping, dict)], lpar_uuid
        )

    async def create_optical_mapping(
        self: StorageClient,
        vios_uuid: str,
        media_name: str,
        lpar_uuid: str,
        target_device: str | None = None,
    ) -> dict[str, Any] | None:
        """Create an optical-media mapping and return its response entry, if any."""
        lpar_link = self.get_lpar_link(lpar_uuid)
        document = build_virtual_optical_mapping_document(
            media_name,
            lpar_link,
            target_device=target_device,
        )
        _reject_non_uuid_path_argument("vios_uuid", vios_uuid)
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}?group=ViosSCSIMapping"
        response = await _append_vios_mapping(
            self, "create_optical_mapping", path, {"vios_uuid": vios_uuid}, document
        )
        entries = _parse_feed(response, path) if response else []
        return entries[0].get("Resource", entries[0]) if entries else None
