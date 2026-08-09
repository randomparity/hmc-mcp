"""MCP tools for read-only inventory and job status, plus the HMC CLI escape hatch.
"""

from __future__ import annotations

from typing import Any

from ._app import (
    _READ_ONLY,
    _run,
    mcp,
    with_client,
)

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
    return _run(lambda: run_hmc_cli(cmd))




@mcp.tool(annotations=_READ_ONLY)
def hmc_console_info() -> dict[str, Any] | None:
    """Get HMC version, network configuration and links to managed systems.

    Useful as a connectivity check — this is the cheapest HMC call.
    """

    return with_client(lambda hmc: hmc.get_console_info())


@mcp.tool(annotations=_READ_ONLY)
def hmc_systems(system_uuid: str | None = None) -> Any:
    """List all managed systems or get one by UUID.

    When system_uuid is omitted, returns a list of all managed systems known to
    the HMC — each entry has UUID, SystemName, State, MTMS (machine
    type/model/serial), IPAddress, etc.

    When system_uuid is provided, returns the full details dict for that one
    system (same fields), or None if not found.
    """
    if system_uuid is None:
        return with_client(lambda hmc: hmc.list_managed_systems())
    return with_client(lambda hmc: hmc.get_managed_system(system_uuid))


@mcp.tool(annotations=_READ_ONLY)
def hmc_lpars(
    system_uuid: str | None = None,
    lpar_uuid: str | None = None,
    name: str | None = None,
    state_only: bool = False,
) -> Any:
    """List logical partitions (LPARs) or get/find one.

    Resolution priority (first match wins):

    1. lpar_uuid + state_only=True  →  str | None  — current LPAR state only
       (uses the cheap quick-property endpoint; equivalent to the former
       hmc_lpar_state tool).
    2. lpar_uuid                    →  dict | None  — full LPAR details.
    3. name                         →  dict | None  — find by PartitionName
       (exact match).
    4. system_uuid                  →  list[dict]   — all LPARs on that system.
    5. (no arguments)               →  list[dict]   — all LPARs known to the HMC.

    Raises ValueError if state_only=True is supplied without lpar_uuid.
    """
    if lpar_uuid is not None and state_only:
        return with_client(
            lambda hmc: hmc.get_quick_property("LogicalPartition", lpar_uuid, "PartitionState")
        )
    if lpar_uuid is not None:
        return with_client(lambda hmc: hmc.get_logical_partition(lpar_uuid))
    if name is not None:
        return with_client(lambda hmc: hmc.find_partition_by_name(name))
    if state_only:
        raise ValueError("state_only=True requires lpar_uuid to be provided")
    return with_client(lambda hmc: hmc.list_logical_partitions(system_uuid))


@mcp.tool(annotations=_READ_ONLY)
def hmc_vios(
    system_uuid: str | None = None,
    vios_uuid: str | None = None,
) -> Any:
    """List Virtual I/O Servers or get storage-detail mappings for one.

    When vios_uuid is provided, returns the VIOS device mapping facts
    (vSCSI, NPIV, virtual optical) for that VIOS — equivalent to the former
    hmc_vios_mappings tool.

    When vios_uuid is omitted, returns a list of all VIOS entries, optionally
    restricted to one managed system via system_uuid.
    """
    if vios_uuid is not None:
        return with_client(lambda hmc: hmc.get_vios_storage_detail(vios_uuid))
    return with_client(lambda hmc: hmc.list_vios(system_uuid))


@mcp.tool(annotations=_READ_ONLY)
def hmc_list_resources(resource_type: str) -> list[dict[str, Any]]:
    """List any uom resource type exposed by the HMC.

    Examples: ManagedSystem, LogicalPartition, VirtualIOServer,
    LogicalPartitionProfile, VirtualSwitch, VirtualNetwork, SharedMemoryPool,
    SharedProcessorPool, HostEthernetAdapter, SRIOVAdapter, Cluster.
    """

    return with_client(lambda hmc: hmc.list_uom(resource_type))




@mcp.tool(annotations=_READ_ONLY)
def hmc_get_job(job_uuid: str) -> dict[str, Any] | None:
    """Get the status/result of an HMC job by UUID."""

    return with_client(lambda hmc: hmc.get_job(job_uuid))
