"""HMCClient adapters mixin.

The full client is assembled in :mod:`hmc_mcp.client` by inheriting every
domain mixin; this module only defines methods for adapters.
"""

from __future__ import annotations

from typing import Any

from .templates import (
    build_client_network_adapter_document,
    build_vfc_adapter_document,
    build_vscsi_adapter_document,
)


class AdaptersMixin:
    async def add_vscsi_adapter(
        self, lpar_uuid: str, vios_partition_id: int, vios_slot: int, slot_number: int | None = None
    ) -> dict[str, Any] | None:
        """Add a Virtual SCSI client adapter, paired to a VIOS server adapter."""

        xml = build_vscsi_adapter_document(vios_partition_id, vios_slot, slot_number)
        return await self.create_child("LogicalPartition", lpar_uuid, "VirtualSCSIClientAdapter", xml)

    async def add_vfc_adapter(
        self, lpar_uuid: str, vios_partition_id: int, vios_slot: int, slot_number: int | None = None
    ) -> dict[str, Any] | None:
        """Add a Virtual Fibre Channel (NPIV) client adapter, paired to a VIOS."""

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

        xml = build_client_network_adapter_document(
            port_vlan_id, slot_number, virtual_switch_id, tagged, mac_address
        )
        return await self.create_child("LogicalPartition", lpar_uuid, "ClientNetworkAdapter", xml)
