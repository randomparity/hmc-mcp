"""Async IBM HMC REST API client.

Implements the Logon/Logoff session lifecycle plus typed helpers for the
most useful uom resources (ManagementConsole, ManagedSystem, LogicalPartition,
VirtualIOServer, quick properties, search and jobs).
"""

from __future__ import annotations

import warnings
from typing import Any

import httpx

from .config import HMCConfig
from .xmlutil import find_text, parse_feed

WEB_NS = "http://www.ibm.com/xmlns/systems/power/firmware/web/mc/2012_10/"

LOGON_REQUEST_TEMPLATE = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<LogonRequest xmlns="{WEB_NS}" schemaVersion="V1_0">
  <Metadata>
    <Atom/>
  </Metadata>
  <UserID kb="CUR" kxe="false">{{user}}</UserID>
  <Password kb="CUR" kxe="false">{{password}}</Password>
</LogonRequest>
"""

# Media-type fragments used by the HMC API.
MEDIA_WEB = "application/vnd.ibm.powervm.web+xml"
MEDIA_UOM = "application/vnd.ibm.powervm.uom+xml"


class HMCError(Exception):
    """Error returned by the HMC REST API."""

    def __init__(self, message: str, status_code: int | None = None, body: str | None = None):
        self.status_code = status_code
        self.body = body
        detail = message
        if status_code is not None:
            detail = f"{message} (HTTP {status_code})"
        if body:
            # HMC error bodies are XML; pull out the message if possible.
            msg = find_text(body, "Message", "msg", "error") or body[:500]
            detail = f"{detail}: {msg}"
        super().__init__(detail)


class HMCClient:
    """Async context-manager client for one HMC session.

    Usage:
        async with HMCClient(config) as hmc:
            systems = await hmc.list_managed_systems()
    """

    def __init__(self, config: HMCConfig):
        config.validate_credentials()
        self.config = config
        self._session_token: str | None = None
        if not config.verify_ssl:
            warnings.filterwarnings("ignore", message="Unverified HTTPS request")
        self._http = httpx.AsyncClient(
            base_url=config.base_url,
            verify=config.verify_ssl,
            timeout=config.timeout,
            headers={
                "X-Audit-Memento": config.audit_memento,
                # Most HMC builds ignore charset but honour JSON when asked;
                # we stick to the canonical XML representation everywhere.
            },
        )

    # ------------------------------------------------------------------ #
    # Session lifecycle
    # ------------------------------------------------------------------ #

    async def __aenter__(self) -> "HMCClient":
        await self.logon()
        return self

    async def __aexit__(self, *exc_info) -> None:
        try:
            await self.logoff()
        finally:
            await self._http.aclose()

    @property
    def is_logged_on(self) -> bool:
        return self._session_token is not None

    async def logon(self) -> str:
        """Authenticate and store the X-API-Session token."""
        body = LOGON_REQUEST_TEMPLATE.format(
            user=self.config.user, password=self.config.password
        )
        resp = await self._http.put(
            "/rest/api/web/Logon",
            content=body,
            headers={
                "Content-Type": f"{MEDIA_WEB}; type=LogonRequest",
                "Accept": f"{MEDIA_WEB}; type=LogonResponse",
            },
        )
        if resp.status_code != 200:
            raise HMCError("HMC logon failed", resp.status_code, resp.text)
        token = find_text(resp.text, "X-API-Session")
        if not token:
            raise HMCError("HMC logon response did not contain an X-API-Session token")
        self._session_token = token
        self._http.headers["X-API-Session"] = token
        return token

    async def logoff(self) -> None:
        """Invalidate the session token (DELETE the Logon resource)."""
        if not self._session_token:
            return
        try:
            await self._http.delete("/rest/api/web/Logon")
        finally:
            self._session_token = None
            self._http.headers.pop("X-API-Session", None)

    # ------------------------------------------------------------------ #
    # Generic request helpers
    # ------------------------------------------------------------------ #

    def _uom_headers(self, resource_type: str | None) -> dict[str, str]:
        accept = MEDIA_UOM
        if resource_type:
            accept = f"{MEDIA_UOM}; type={resource_type}"
        return {"Accept": accept}

    async def _get(self, path: str, resource_type: str | None = None) -> str:
        resp = await self._http.get(path, headers=self._uom_headers(resource_type))
        if resp.status_code == 204:
            return ""
        if resp.status_code != 200:
            raise HMCError(f"GET {path} failed", resp.status_code, resp.text)
        return resp.text

    async def _post(
        self,
        path: str,
        body: str | bytes,
        resource_type: str | None = None,
    ) -> str:
        content_type = MEDIA_UOM
        if resource_type:
            content_type = f"{MEDIA_UOM}; type={resource_type}"
        resp = await self._http.post(
            path,
            content=body,
            headers={"Content-Type": content_type, "Accept": content_type},
        )
        if resp.status_code not in (200, 201, 202):
            raise HMCError(f"POST {path} failed", resp.status_code, resp.text)
        return resp.text

    async def _put(
        self,
        path: str,
        body: str | bytes,
        resource_type: str | None = None,
    ) -> str:
        content_type = MEDIA_UOM
        if resource_type:
            content_type = f"{MEDIA_UOM}; type={resource_type}"
        resp = await self._http.put(
            path,
            content=body,
            headers={"Content-Type": content_type, "Accept": content_type},
        )
        if resp.status_code not in (200, 201, 202, 204):
            raise HMCError(f"PUT {path} failed", resp.status_code, resp.text)
        return resp.text

    async def _delete(self, path: str) -> None:
        resp = await self._http.delete(path)
        if resp.status_code not in (200, 202, 204):
            raise HMCError(f"DELETE {path} failed", resp.status_code, resp.text)

    # ------------------------------------------------------------------ #
    # uom resources
    # ------------------------------------------------------------------ #

    async def list_uom(self, resource_type: str, group: str | None = None) -> list[dict[str, Any]]:
        """GET /rest/api/uom/{ResourceType} and parse the Atom feed."""
        path = f"/rest/api/uom/{resource_type}"
        if group:
            path += f"?group={group}"
        xml = await self._get(path, resource_type)
        if not xml:
            return []
        return parse_feed(xml)

    async def get_uom(self, resource_type: str, uuid: str, group: str | None = None) -> dict[str, Any] | None:
        """GET /rest/api/uom/{ResourceType}/{uuid} and parse the entry."""
        path = f"/rest/api/uom/{resource_type}/{uuid}"
        if group:
            path += f"?group={group}"
        xml = await self._get(path, resource_type)
        if not xml:
            return None
        entries = parse_feed(xml)
        return entries[0] if entries else None

    async def get_quick_property(self, resource_type: str, uuid: str, property_name: str) -> Any:
        """GET a quick property, e.g. LogicalPartition/{uuid}/quick/PartitionState."""
        xml = await self._get(f"/rest/api/uom/{resource_type}/{uuid}/quick/{property_name}", resource_type)
        # The response is a tiny XML doc whose root text (or first element) is
        # the value.
        value = find_text(xml, property_name)
        if value is None:
            # Fall back to the whole body text.
            return xml.strip() or None
        return value

    async def search_uom(self, resource_type: str, property_name: str, property_value: str) -> list[dict[str, Any]]:
        """GET /rest/api/uom/{ResourceType}/search/({Property}=={Value})."""
        path = f"/rest/api/uom/{resource_type}/search/({property_name}=={property_value})"
        xml = await self._get(path, resource_type)
        if not xml:
            return []
        return parse_feed(xml)

    # -- Convenience wrappers for the common resources ----------------- #

    async def get_console_info(self) -> dict[str, Any] | None:
        """ManagementConsole: HMC version, network info, links to systems."""
        entries = await self.list_uom("ManagementConsole")
        return entries[0] if entries else None

    async def list_managed_systems(self) -> list[dict[str, Any]]:
        return await self.list_uom("ManagedSystem")

    async def get_managed_system(self, uuid: str) -> dict[str, Any] | None:
        return await self.get_uom("ManagedSystem", uuid)

    async def list_logical_partitions(self, system_uuid: str | None = None) -> list[dict[str, Any]]:
        if system_uuid:
            path = f"/rest/api/uom/ManagedSystem/{system_uuid}/LogicalPartition"
            xml = await self._get(path, "LogicalPartition")
            return parse_feed(xml) if xml else []
        return await self.list_uom("LogicalPartition")

    async def get_logical_partition(self, uuid: str) -> dict[str, Any] | None:
        return await self.get_uom("LogicalPartition", uuid)

    async def find_partition_by_name(self, name: str) -> dict[str, Any] | None:
        results = await self.search_uom("LogicalPartition", "PartitionName", name)
        return results[0] if results else None

    async def create_logical_partition(
        self, system_uuid: str, lpar_xml: str
    ) -> dict[str, Any] | None:
        """Create an LPAR on a managed system.

        PUTs a LogicalPartition document (see templates.build_lpar_document)
        to /rest/api/uom/ManagedSystem/{system_uuid}/LogicalPartition and
        returns the created partition entry.
        """
        xml = await self._put(
            f"/rest/api/uom/ManagedSystem/{system_uuid}/LogicalPartition",
            lpar_xml,
            resource_type="LogicalPartition",
        )
        entries = parse_feed(xml) if xml else []
        return entries[0] if entries else None

    async def modify_logical_partition(
        self, lpar_uuid: str, lpar_xml: str
    ) -> dict[str, Any] | None:
        """Modify an LPAR's properties (POST a partial LogicalPartition doc).

        Memory/CPU changes to a *running* partition only take effect if the
        partition supports dynamic LPAR (DLPAR) and RMC is up; otherwise the
        change lands in the profile for the next activation.
        """
        xml = await self._post(
            f"/rest/api/uom/LogicalPartition/{lpar_uuid}",
            lpar_xml,
            resource_type="LogicalPartition",
        )
        entries = parse_feed(xml) if xml else []
        return entries[0] if entries else None

    async def delete_logical_partition(self, lpar_uuid: str) -> None:
        """Delete an LPAR. It must be powered off first."""
        await self._delete(f"/rest/api/uom/LogicalPartition/{lpar_uuid}")

    # ------------------------------------------------------------------ #
    # Virtual adapters (children of LogicalPartition)
    # ------------------------------------------------------------------ #

    async def list_child(self, parent_type: str, parent_uuid: str, child_type: str) -> list[dict[str, Any]]:
        """GET /rest/api/uom/{parent}/{uuid}/{child} and parse the feed."""
        path = f"/rest/api/uom/{parent_type}/{parent_uuid}/{child_type}"
        xml = await self._get(path, child_type)
        return parse_feed(xml) if xml else []

    async def create_child(
        self, parent_type: str, parent_uuid: str, child_type: str, child_xml: str
    ) -> dict[str, Any] | None:
        """PUT a child resource (e.g. a virtual adapter) under a parent."""
        path = f"/rest/api/uom/{parent_type}/{parent_uuid}/{child_type}"
        xml = await self._put(path, child_xml, resource_type=child_type)
        entries = parse_feed(xml) if xml else []
        return entries[0] if entries else None

    async def delete_child(
        self, parent_type: str, parent_uuid: str, child_type: str, child_uuid: str
    ) -> None:
        """DELETE a child resource instance."""
        await self._delete(f"/rest/api/uom/{parent_type}/{parent_uuid}/{child_type}/{child_uuid}")

    async def add_vscsi_adapter(
        self, lpar_uuid: str, vios_partition_id: int, vios_slot: int, slot_number: int | None = None
    ) -> dict[str, Any] | None:
        """Add a Virtual SCSI client adapter, paired to a VIOS server adapter."""
        from .templates import build_vscsi_adapter_document

        xml = build_vscsi_adapter_document(vios_partition_id, vios_slot, slot_number)
        return await self.create_child("LogicalPartition", lpar_uuid, "VirtualSCSIClientAdapter", xml)

    async def add_vfc_adapter(
        self, lpar_uuid: str, vios_partition_id: int, vios_slot: int, slot_number: int | None = None
    ) -> dict[str, Any] | None:
        """Add a Virtual Fibre Channel (NPIV) client adapter, paired to a VIOS."""
        from .templates import build_vfc_adapter_document

        xml = build_vfc_adapter_document(vios_partition_id, vios_slot, slot_number)
        return await self.create_child("LogicalPartition", lpar_uuid, "VirtualFibreChannelClientAdapter", xml)

    async def add_network_adapter(
        self,
        lpar_uuid: str,
        port_vlan_id: int,
        slot_number: int | None = None,
        virtual_switch_id: int | None = None,
        tagged: bool = False,
        mac_address: str | None = None,
    ) -> dict[str, Any] | None:
        """Add a Virtual Ethernet client network adapter to an LPAR."""
        from .templates import build_client_network_adapter_document

        xml = build_client_network_adapter_document(
            port_vlan_id, slot_number, virtual_switch_id, tagged, mac_address
        )
        return await self.create_child("LogicalPartition", lpar_uuid, "ClientNetworkAdapter", xml)

    # ------------------------------------------------------------------ #
    # Virtual storage (children of VirtualIOServer)
    # ------------------------------------------------------------------ #

    async def get_vios_link(self, vios_uuid: str) -> str:
        """Atom SELF href for a VIOS (used when building mappings)."""
        return f"{self.config.base_url}/rest/api/uom/VirtualIOServer/{vios_uuid}"

    async def get_lpar_link(self, lpar_uuid: str) -> str:
        """Atom SELF href for an LPAR (used when building mappings)."""
        return f"{self.config.base_url}/rest/api/uom/LogicalPartition/{lpar_uuid}"

    async def list_volume_groups(self, vios_uuid: str) -> list[dict[str, Any]]:
        """List Volume Groups on a VIOS (free space, PVs, virtual disks)."""
        xml = await self._get(
            f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup", "VolumeGroup"
        )
        return parse_feed(xml) if xml else []

    async def get_volume_group(self, vios_uuid: str, vg_uuid: str) -> dict[str, Any] | None:
        return await self.get_uom_path(
            f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}", "VolumeGroup"
        )

    async def get_uom_path(self, path: str, resource_type: str) -> dict[str, Any] | None:
        xml = await self._get(path, resource_type)
        if not xml:
            return None
        entries = parse_feed(xml)
        return entries[0] if entries else None

    async def create_volume_group(
        self, vios_uuid: str, name: str, physical_volumes: list[str]
    ) -> dict[str, Any] | None:
        """Create a Volume Group on a VIOS from physical volumes (e.g. ['hdisk10'])."""
        from .templates import build_volume_group_document

        xml = build_volume_group_document(name, physical_volumes)
        resp = await self._put(
            f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup", xml, resource_type="VolumeGroup"
        )
        entries = parse_feed(resp) if resp else []
        return entries[0] if entries else None

    async def create_virtual_disk(
        self, vios_uuid: str, vg_uuid: str, disk_name: str, capacity_mb: int
    ) -> dict[str, Any] | None:
        """Create a Virtual Disk (logical volume) in a Volume Group."""
        from .templates import build_virtual_disk_document

        xml = build_virtual_disk_document(disk_name, capacity_mb)
        resp = await self._post(
            f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}",
            xml, resource_type="VolumeGroup",
        )
        entries = parse_feed(resp) if resp else []
        return entries[0] if entries else None

    async def map_storage_to_lpar(
        self,
        vios_uuid: str,
        storage_kind: str,
        storage_name: str,
        lpar_uuid: str,
        target_device: str | None = None,
    ) -> dict[str, Any] | None:
        """Create a VirtualSCSIMapping connecting backing storage to an LPAR.

        storage_kind is "PhysicalVolume" (whole hdisk) or "VirtualDisk" (a
        logical volume created with create_virtual_disk). storage_name is the
        device or disk name. lpar_uuid is the client partition to attach to.
        """
        from .templates import build_vscsi_mapping_document

        lpar_link = await self.get_lpar_link(lpar_uuid)
        xml = build_vscsi_mapping_document(
            storage_kind, storage_name, lpar_link, target_device=target_device
        )
        resp = await self._post(
            f"/rest/api/uom/VirtualIOServer/{vios_uuid}", xml, resource_type="VirtualIOServer"
        )
        entries = parse_feed(resp) if resp else []
        return entries[0] if entries else None

    async def list_vios(self, system_uuid: str | None = None) -> list[dict[str, Any]]:
        if system_uuid:
            path = f"/rest/api/uom/ManagedSystem/{system_uuid}/VirtualIOServer"
            xml = await self._get(path, "VirtualIOServer")
            return parse_feed(xml) if xml else []
        return await self.list_uom("VirtualIOServer")

    # ------------------------------------------------------------------ #
    # Jobs (long-running operations)
    # ------------------------------------------------------------------ #

    async def submit_job(self, job_path: str, job_request_xml: str) -> dict[str, Any] | None:
        """POST a JobRequest to /rest/api/uom/.../do/{Operation} and return the job.

        `job_path` is the full do-path, e.g.
        /rest/api/uom/LogicalPartition/{uuid}/do/PowerOn
        """
        xml = await self._post(job_path, job_request_xml, resource_type="JobRequest")
        entries = parse_feed(xml)
        return entries[0] if entries else None

    async def get_job(self, job_uuid: str) -> dict[str, Any] | None:
        return await self.get_uom("Job", job_uuid)

    async def delete_job(self, job_uuid: str) -> None:
        await self._delete(f"/rest/api/uom/Job/{job_uuid}")

    # ------------------------------------------------------------------ #
    # Raw escape hatch
    # ------------------------------------------------------------------ #

    async def raw_get(self, path: str, accept: str = "*/*") -> str:
        resp = await self._http.get(path, headers={"Accept": accept})
        if resp.status_code == 204:
            return ""
        if resp.status_code != 200:
            raise HMCError(f"GET {path} failed", resp.status_code, resp.text)
        return resp.text

    async def raw_post(self, path: str, body: str, content_type: str = "application/xml") -> str:
        resp = await self._http.post(path, content=body, headers={"Content-Type": content_type})
        if resp.status_code not in (200, 201, 202):
            raise HMCError(f"POST {path} failed", resp.status_code, resp.text)
        return resp.text
