"""MCP adapter for the end-to-end LPAR provisioning operation."""

from __future__ import annotations

from typing import Any

from ._app import _run, mcp
from .common import client_from_env
from .documents import PartitionType, StorageKind
from .operations_provision import provision_lpar


@mcp.tool
def hmc_provision_lpar(
    system_name_or_uuid: str,
    name: str,
    port_vlan_id: int,
    vios_uuid: str,
    vios_partition_id: int,
    vios_slot: int,
    storage_name: str,
    partition_type: PartitionType = "AIX/Linux",
    min_memory: int = 256,
    desired_memory: int = 4096,
    max_memory: int = 8192,
    desired_vcpus: int = 1,
    max_vcpus: int = 2,
    storage_kind: StorageKind = "VirtualDisk",
    vg_uuid: str | None = None,
    power_on: bool = True,
    dry_run: bool = False,
    profile: str | None = None,
) -> dict[str, Any]:
    """Provision an LPAR with network, vSCSI storage, and optional power-on."""

    async def _go():
        async with client_from_env(profile) as hmc:
            return await provision_lpar(
                hmc,
                system_name_or_uuid=system_name_or_uuid,
                name=name,
                port_vlan_id=port_vlan_id,
                vios_uuid=vios_uuid,
                vios_partition_id=vios_partition_id,
                vios_slot=vios_slot,
                storage_name=storage_name,
                partition_type=partition_type,
                min_memory=min_memory,
                desired_memory=desired_memory,
                max_memory=max_memory,
                desired_vcpus=desired_vcpus,
                max_vcpus=max_vcpus,
                storage_kind=storage_kind,
                vg_uuid=vg_uuid,
                power_on=power_on,
                dry_run=dry_run,
            )

    return _run(_go)
