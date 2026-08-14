"""MCP adapter for the end-to-end LPAR provisioning operation."""

from __future__ import annotations

from typing import Any

from ._app import _run, mcp
from .common import client_from_env
from .documents import LparResources, PartitionType
from .operations_provision import ProvisionNetwork, ProvisionStorage, provision_lpar


@mcp.tool
def hmc_provision_lpar(
    system_name_or_uuid: str,
    name: str,
    network: ProvisionNetwork,
    storage: ProvisionStorage,
    resources: LparResources = LparResources(
        min_memory=256,
        desired_memory=4096,
        max_memory=8192,
        desired_vcpus=1,
        max_vcpus=2,
    ),
    partition_type: PartitionType = "AIX/Linux",
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
                network=network,
                storage=storage,
                resources=resources,
                partition_type=partition_type,
                power_on=power_on,
                dry_run=dry_run,
            )

    return _run(_go)
