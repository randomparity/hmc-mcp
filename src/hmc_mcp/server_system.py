"""MCP tools for read-only inventory, HMC CLI passthrough, and job submission.
"""

from __future__ import annotations

from typing import Any

from ._app import (
    _DESTRUCTIVE,
    _READ_ONLY,
    _run,
    mcp,
    with_client,
)

from .jobs import power_off_lpar_job, power_on_lpar_job
from .ssh import run_hmc_cli



@mcp.tool
def hmc_run_command(cmd: str) -> str:
    """Execute an arbitrary HMC CLI command over SSH and return its output.

    WARNING: This tool executes arbitrary commands on the HMC with the
    credentials configured in HMC_USER / HMC_PASSWORD (or HMC_SSH_KEY_FILE).
    It is an operator escape-hatch equivalent to Ansible ``hmc_command``.
    Use only for HMC CLI operations that have no dedicated MCP tool.

    Authentication follows the same env-var configuration as all other tools:
    set HMC_SSH_KEY_FILE to use key-based auth, otherwise password auth is used.

    Reference: https://www.ibm.com/docs/en/power10/7063-CR1?topic=hmc-commands
    """
    return _run(run_hmc_cli(cmd))




@mcp.tool(annotations=_READ_ONLY)
def hmc_console_info() -> dict[str, Any] | None:
    """Get HMC version, network configuration and links to managed systems.

    Useful as a connectivity check — this is the cheapest HMC call.
    """

    return with_client(lambda hmc: hmc.get_console_info())


@mcp.tool(annotations=_READ_ONLY)
def hmc_list_systems() -> list[dict[str, Any]]:
    """List all managed systems (Power servers) known to the HMC.

    Returns one entry per system with UUID, SystemName, State, MTMS
    (machine type/model/serial), IPAddress, etc.
    """

    return with_client(lambda hmc: hmc.list_managed_systems())


@mcp.tool(annotations=_READ_ONLY)
def hmc_get_system(system_uuid: str) -> dict[str, Any] | None:
    """Get full details for one managed system by UUID."""

    return with_client(lambda hmc: hmc.get_managed_system(system_uuid))


@mcp.tool(annotations=_READ_ONLY)
def hmc_list_lpars(system_uuid: str | None = None) -> list[dict[str, Any]]:
    """List logical partitions (LPARs).

    If system_uuid is omitted, lists every LPAR known to the HMC; otherwise
    only the LPARs of that managed system.
    """

    return with_client(lambda hmc: hmc.list_logical_partitions(system_uuid))


@mcp.tool(annotations=_READ_ONLY)
def hmc_get_lpar(lpar_uuid: str) -> dict[str, Any] | None:
    """Get full details for one logical partition by UUID."""

    return with_client(lambda hmc: hmc.get_logical_partition(lpar_uuid))


@mcp.tool(annotations=_READ_ONLY)
def hmc_find_lpar(name: str) -> dict[str, Any] | None:
    """Find a logical partition by its partition name (exact match)."""

    return with_client(lambda hmc: hmc.find_partition_by_name(name))


@mcp.tool(annotations=_READ_ONLY)
def hmc_lpar_state(lpar_uuid: str) -> str | None:
    """Get just the current state of an LPAR (running, not activated, ...).

    Uses the cheap quick-property endpoint instead of a full fetch.
    """

    return with_client(
        lambda hmc: hmc.get_quick_property("LogicalPartition", lpar_uuid, "PartitionState")
    )


@mcp.tool(annotations=_READ_ONLY)
def hmc_list_vios(system_uuid: str | None = None) -> list[dict[str, Any]]:
    """List Virtual I/O Servers, optionally restricted to one managed system."""

    return with_client(lambda hmc: hmc.list_vios(system_uuid))


@mcp.tool(annotations=_READ_ONLY)
def hmc_vios_mappings(vios_uuid: str) -> dict[str, Any] | None:
    """Return VIOS device mapping facts (vSCSI, NPIV, virtual optical).

    Fetches the ViosStorageDetail group for the given VIOS UUID and returns
    the parsed entry, which contains VirtualSCSIMappings (physical volumes and
    virtual disks served to LPARs) and VirtualFibreChannelMappings (NPIV port
    mappings). Equivalent to Ansible ``vios_mapping_facts``.

    Find VIOS UUIDs with hmc_list_vios.
    """

    return with_client(lambda hmc: hmc.get_vios_storage_detail(vios_uuid))


@mcp.tool(annotations=_READ_ONLY)
def hmc_list_resources(resource_type: str) -> list[dict[str, Any]]:
    """List any uom resource type exposed by the HMC.

    Examples: ManagedSystem, LogicalPartition, VirtualIOServer,
    LogicalPartitionProfile, VirtualSwitch, VirtualNetwork, SharedMemoryPool,
    SharedProcessorPool, HostEthernetAdapter, SRIOVAdapter, Cluster.
    """

    return with_client(lambda hmc: hmc.list_uom(resource_type))




@mcp.tool
def hmc_power_on_lpar(lpar_uuid: str) -> dict[str, Any] | None:
    """Submit a PowerOn job for a logical partition.

    Returns the submitted job (check hmc_get_job for status). This changes
    the state of a real partition — confirm the UUID with hmc_find_lpar
    before calling.
    """

    return with_client(
        lambda hmc: hmc.submit_job(
            f"/rest/api/uom/LogicalPartition/{lpar_uuid}/do/PowerOn",
            power_on_lpar_job(),
        )
    )


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_power_off_lpar(lpar_uuid: str, immediate: bool = False) -> dict[str, Any] | None:
    """Submit a PowerOff job for a logical partition.

    immediate=True forces an immediate power off (no graceful OS shutdown).
    Returns the submitted job. This changes the state of a real partition.
    """

    return with_client(
        lambda hmc: hmc.submit_job(
            f"/rest/api/uom/LogicalPartition/{lpar_uuid}/do/PowerOff",
            power_off_lpar_job(immediate=immediate),
        )
    )


@mcp.tool(annotations=_READ_ONLY)
def hmc_get_job(job_uuid: str) -> dict[str, Any] | None:
    """Get the status/result of an HMC job by UUID."""

    return with_client(lambda hmc: hmc.get_job(job_uuid))


