"""MCP tools for read-only inventory, HMC CLI passthrough, and job submission.
"""

from __future__ import annotations

from typing import Any

from ._app import (
    _DESTRUCTIVE,
    _READ_ONLY,
    _run,
    mcp,
)

from .common import client_from_env
from .config import HMCConfig
from .jobs import power_off_lpar_job, power_on_lpar_job
from .ssh import run_hmc_command



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
    config = HMCConfig()
    return _run(run_hmc_command(config, cmd))




@mcp.tool(annotations=_READ_ONLY)
def hmc_console_info() -> dict[str, Any] | None:
    """Get HMC version, network configuration and links to managed systems.

    Useful as a connectivity check — this is the cheapest HMC call.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.get_console_info()

    return _run(_go())


@mcp.tool(annotations=_READ_ONLY)
def hmc_list_systems() -> list[dict[str, Any]]:
    """List all managed systems (Power servers) known to the HMC.

    Returns one entry per system with UUID, SystemName, State, MTMS
    (machine type/model/serial), IPAddress, etc.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.list_managed_systems()

    return _run(_go())


@mcp.tool(annotations=_READ_ONLY)
def hmc_get_system(system_uuid: str) -> dict[str, Any] | None:
    """Get full details for one managed system by UUID."""

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.get_managed_system(system_uuid)

    return _run(_go())


@mcp.tool(annotations=_READ_ONLY)
def hmc_list_lpars(system_uuid: str | None = None) -> list[dict[str, Any]]:
    """List logical partitions (LPARs).

    If system_uuid is omitted, lists every LPAR known to the HMC; otherwise
    only the LPARs of that managed system.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.list_logical_partitions(system_uuid)

    return _run(_go())


@mcp.tool(annotations=_READ_ONLY)
def hmc_get_lpar(lpar_uuid: str) -> dict[str, Any] | None:
    """Get full details for one logical partition by UUID."""

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.get_logical_partition(lpar_uuid)

    return _run(_go())


@mcp.tool(annotations=_READ_ONLY)
def hmc_find_lpar(name: str) -> dict[str, Any] | None:
    """Find a logical partition by its partition name (exact match)."""

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.find_partition_by_name(name)

    return _run(_go())


@mcp.tool(annotations=_READ_ONLY)
def hmc_lpar_state(lpar_uuid: str) -> Any:
    """Get just the current state of an LPAR (running, not activated, ...).

    Uses the cheap quick-property endpoint instead of a full fetch.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.get_quick_property("LogicalPartition", lpar_uuid, "PartitionState")

    return _run(_go())


@mcp.tool(annotations=_READ_ONLY)
def hmc_list_vios(system_uuid: str | None = None) -> list[dict[str, Any]]:
    """List Virtual I/O Servers, optionally restricted to one managed system."""

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.list_vios(system_uuid)

    return _run(_go())


@mcp.tool(annotations=_READ_ONLY)
def hmc_vios_mappings(vios_uuid: str) -> dict[str, Any] | None:
    """Return VIOS device mapping facts (vSCSI, NPIV, virtual optical).

    Fetches the ViosStorageDetail group for the given VIOS UUID and returns
    the parsed entry, which contains VirtualSCSIMappings (physical volumes and
    virtual disks served to LPARs) and VirtualFibreChannelMappings (NPIV port
    mappings). Equivalent to Ansible ``vios_mapping_facts``.

    Find VIOS UUIDs with hmc_list_vios.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.get_vios_storage_detail(vios_uuid)

    return _run(_go())


@mcp.tool(annotations=_READ_ONLY)
def hmc_list_resources(resource_type: str) -> list[dict[str, Any]]:
    """List any uom resource type exposed by the HMC.

    Examples: ManagedSystem, LogicalPartition, VirtualIOServer,
    LogicalPartitionProfile, VirtualSwitch, VirtualNetwork, SharedMemoryPool,
    SharedProcessorPool, HostEthernetAdapter, SRIOVAdapter, Cluster.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.list_uom(resource_type)

    return _run(_go())




@mcp.tool
def hmc_power_on_lpar(lpar_uuid: str) -> dict[str, Any] | None:
    """Submit a PowerOn job for a logical partition.

    Returns the submitted job (check hmc_get_job for status). This changes
    the state of a real partition — confirm the UUID with hmc_find_lpar
    before calling.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.submit_job(
                f"/rest/api/uom/LogicalPartition/{lpar_uuid}/do/PowerOn",
                power_on_lpar_job(),
            )

    return _run(_go())


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_power_off_lpar(lpar_uuid: str, immediate: bool = False) -> dict[str, Any] | None:
    """Submit a PowerOff job for a logical partition.

    immediate=True forces an immediate power off (no graceful OS shutdown).
    Returns the submitted job. This changes the state of a real partition.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.submit_job(
                f"/rest/api/uom/LogicalPartition/{lpar_uuid}/do/PowerOff",
                power_off_lpar_job(immediate=immediate),
            )

    return _run(_go())


@mcp.tool(annotations=_READ_ONLY)
def hmc_get_job(job_uuid: str) -> dict[str, Any] | None:
    """Get the status/result of an HMC job by UUID."""

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.get_job(job_uuid)

    return _run(_go())


