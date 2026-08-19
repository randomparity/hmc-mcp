"""MCP adapter for the end-to-end LPAR provisioning operation."""

from __future__ import annotations

from .tool_registry import tool_module

from ._app import _run
from .common import client_from_env
from .documents import LparResources, PartitionType
from .operations_provision import (
    ProvisionNetwork,
    ProvisionResult,
    ProvisionStorage,
    provision_lpar,
)


tool, register_tools, tool_security = tool_module()


# Not exhaustive: the VIOS this call mutates is named by `storage.vios_uuid` and
# `network.vios_partition_id`, one level below the signature, so `build_targets`
# cannot see them and no policy `targets` table can bound them. The mapping is
# POSTed to the global /VirtualIOServer/{uuid} URI, so nothing constrains that
# VIOS to `system_name_or_uuid` either. ADR 0039 grants this tool only under
# `targets = "all-targets"`.
@tool(
    effect="mutate",
    operation="provision.lpar",
    target_kind="managed_system",
    exhaustive_targets=False,
)
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
) -> ProvisionResult:
    """Provision an LPAR with network, vSCSI storage, and optional power-on.

    Args:
        system_name_or_uuid: Target managed-system name or UUID.
        name: Name for the new logical partition.
        network: Virtual Ethernet and VIOS vSCSI attachment settings.
        storage: VIOS-backed storage mapping settings.
        resources: Memory and processor settings for the partition.
        partition_type: Partition environment: AIX/Linux, OS400, or VIOS.
        power_on: Power on the partition after configuration succeeds.
        dry_run: Validate preconditions without creating or changing resources.
        profile: Optional TOML profile name; uses environment defaults when omitted.

    Returns:
        A structured result with resource_created, workflow_completed, lpar_uuid,
        dry_run, ownership_stamped, steps, and warnings fields.
    """

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
