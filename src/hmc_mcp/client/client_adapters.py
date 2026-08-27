"""HMCClient adapters mixin.

The full client is assembled in :mod:`hmc_mcp.client` by inheriting every
domain mixin; this module only defines methods for adapters.
"""

from __future__ import annotations

from typing import Any, Literal, get_args

from .client_contracts import AdaptersClient
from ..documents import (
    build_client_network_adapter_document,
    build_vfc_adapter_document,
    build_vscsi_adapter_document,
)

AdapterType = Literal[
    "ClientNetworkAdapter",
    "VirtualSCSIClientAdapter",
    "VirtualFibreChannelClientAdapter",
    "VirtualNICDedicated",
]
ADAPTER_TYPES = frozenset(get_args(AdapterType))


def validate_adapter_type(adapter_type: AdapterType) -> AdapterType:
    if adapter_type not in ADAPTER_TYPES:
        raise ValueError(
            f"Invalid adapter_type {adapter_type!r}. "
            f"Must be one of: {', '.join(sorted(ADAPTER_TYPES))}"
        )
    return adapter_type


class AdaptersMixin:
    async def list_adapters(
        self: AdaptersClient, lpar_uuid: str, adapter_type: AdapterType
    ) -> list[dict[str, Any]]:
        validate_adapter_type(adapter_type)
        return await self.list_child("LogicalPartition", lpar_uuid, adapter_type)

    async def delete_adapter(
        self: AdaptersClient,
        lpar_uuid: str,
        adapter_type: AdapterType,
        adapter_uuid: str,
    ) -> None:
        validate_adapter_type(adapter_type)
        await self.delete_child(
            "LogicalPartition", lpar_uuid, adapter_type, adapter_uuid
        )

    async def add_vscsi_adapter(
        self: AdaptersClient,
        lpar_uuid: str,
        vios_partition_id: int,
        vios_slot: int,
        slot_number: int | None = None,
    ) -> dict[str, Any] | None:
        """Add a Virtual SCSI client adapter, paired to a VIOS server adapter."""

        xml = build_vscsi_adapter_document(vios_partition_id, vios_slot, slot_number)
        return await self.create_child(
            "LogicalPartition", lpar_uuid, "VirtualSCSIClientAdapter", xml
        )

    async def add_vfc_adapter(
        self: AdaptersClient,
        lpar_uuid: str,
        vios_partition_id: int,
        vios_slot: int,
        slot_number: int | None = None,
    ) -> dict[str, Any] | None:
        """Add a Virtual Fibre Channel (NPIV) client adapter, paired to a VIOS."""

        xml = build_vfc_adapter_document(vios_partition_id, vios_slot, slot_number)
        return await self.create_child(
            "LogicalPartition", lpar_uuid, "VirtualFibreChannelClientAdapter", xml
        )

    async def add_network_adapter(
        self: AdaptersClient,
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
        return await self.create_child(
            "LogicalPartition", lpar_uuid, "ClientNetworkAdapter", xml
        )
