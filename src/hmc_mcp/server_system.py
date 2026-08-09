"""MCP tools for read-only inventory and job status, plus the HMC CLI escape hatch.
"""

from __future__ import annotations

from typing import Any

from ._app import (
    _READ_ONLY,
    _resolve_lpar_uuid,
    _resolve_system_uuid,
    _resolve_vios_uuid,
    _run,
    mcp,
    with_client,
)
from .common import client_from_env

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
def hmc_systems(system_name_or_uuid: str | None = None) -> Any:
    """List all managed systems or get one by name or UUID.

    When system_name_or_uuid is omitted, returns a list of all managed systems
    known to the HMC — each entry has UUID, SystemName, State, MTMS (machine
    type/model/serial), IPAddress, etc.

    When system_name_or_uuid is provided, accepts either a SystemName or a UUID
    and returns the full details dict for that one system, or None if not found.
    """
    if system_name_or_uuid is None:
        return with_client(lambda hmc: hmc.list_managed_systems())

    async def _go():
        async with client_from_env() as hmc:
            system_uuid = await _resolve_system_uuid(hmc, system_name_or_uuid)
            return await hmc.get_managed_system(system_uuid)

    return _run(_go)


@mcp.tool(annotations=_READ_ONLY)
def hmc_lpars(
    system_name_or_uuid: str | None = None,
    lpar_name_or_uuid: str | None = None,
    name: str | None = None,
    state_only: bool = False,
) -> Any:
    """List logical partitions (LPARs) or get/find one.

    Resolution priority (first match wins):

    1. lpar_name_or_uuid + state_only=True  →  str | None  — current LPAR state
       only (uses the cheap quick-property endpoint instead of a full fetch).
    2. lpar_name_or_uuid                    →  dict | None  — full LPAR details.
       Accepts either a PartitionName or a UUID. When both lpar_name_or_uuid
       and name are supplied, lpar_name_or_uuid takes priority and name is
       ignored.
    3. name                                 →  dict | None  — find by
       PartitionName (exact match).
    4. system_name_or_uuid                  →  list[dict]   — all LPARs on that
       system. Accepts either a SystemName or a UUID.
    5. (no arguments)                       →  list[dict]   — all LPARs known
       to the HMC.

    Raises ValueError if state_only=True is supplied without lpar_name_or_uuid.
    """
    if lpar_name_or_uuid is not None and state_only:
        async def _go_state():
            async with client_from_env() as hmc:
                lpar_uuid = await _resolve_lpar_uuid(hmc, lpar_name_or_uuid)
                return await hmc.get_quick_property("LogicalPartition", lpar_uuid, "PartitionState")
        return _run(_go_state)
    if lpar_name_or_uuid is not None:
        async def _go_lpar():
            async with client_from_env() as hmc:
                lpar_uuid = await _resolve_lpar_uuid(hmc, lpar_name_or_uuid)
                return await hmc.get_logical_partition(lpar_uuid)
        return _run(_go_lpar)
    if name is not None:
        return with_client(lambda hmc: hmc.find_partition_by_name(name))
    if state_only:
        raise ValueError("state_only=True requires lpar_name_or_uuid to be provided")
    if system_name_or_uuid is not None:
        async def _go_sys():
            async with client_from_env() as hmc:
                system_uuid = await _resolve_system_uuid(hmc, system_name_or_uuid)
                return await hmc.list_logical_partitions(system_uuid)
        return _run(_go_sys)
    return with_client(lambda hmc: hmc.list_logical_partitions(None))


@mcp.tool(annotations=_READ_ONLY)
def hmc_vios(
    system_name_or_uuid: str | None = None,
    vios_name_or_uuid: str | None = None,
) -> Any:
    """List Virtual I/O Servers or get storage-detail mappings for one.

    When vios_name_or_uuid is provided, accepts either a PartitionName or a
    UUID and returns the VIOS device mapping facts (vSCSI, NPIV, virtual
    optical) for that VIOS.

    When vios_name_or_uuid is omitted, returns a list of all VIOS entries,
    optionally restricted to one managed system via system_name_or_uuid
    (accepts either a SystemName or a UUID).
    """
    if vios_name_or_uuid is not None:
        async def _go_vios():
            async with client_from_env() as hmc:
                vios_uuid = await _resolve_vios_uuid(hmc, vios_name_or_uuid)
                return await hmc.get_vios_storage_detail(vios_uuid)
        return _run(_go_vios)
    if system_name_or_uuid is not None:
        async def _go_sys():
            async with client_from_env() as hmc:
                system_uuid = await _resolve_system_uuid(hmc, system_name_or_uuid)
                return await hmc.list_vios(system_uuid)
        return _run(_go_sys)
    return with_client(lambda hmc: hmc.list_vios(None))


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


@mcp.tool(annotations=_READ_ONLY)
def hmc_recent_jobs(limit: int = 20) -> list[dict[str, Any]]:
    """List recent HMC jobs (most recent first, up to *limit* entries).

    Returns a list of parsed job dicts with at minimum JobID and Status.
    Useful for auditing recent HMC activity — power operations, firmware
    updates, migrations, etc.
    """
    jobs = with_client(lambda hmc: hmc.list_uom("Job"))
    return jobs[:limit]


def _system_capacity(system: dict[str, Any], lpars: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute capacity stats for one managed system from its entry + LPAR list."""
    res = system.get("Resource") or {}
    total_mem = int(res.get("AssignableSystemMemory") or 0)
    total_procs = float(res.get("ConfigurableSystemProcessorUnits") or 0.0)

    assigned_mem = 0
    assigned_procs = 0.0
    running = 0
    for lpar in lpars:
        lr = lpar.get("Resource") or {}
        assigned_mem += int(lr.get("DesiredMemory") or 0)
        try:
            assigned_procs += float(lr.get("DesiredProcessingUnits") or 0.0)
        except (TypeError, ValueError):
            pass
        if lr.get("PartitionState") == "running":
            running += 1

    return {
        "system_uuid": system.get("UUID"),
        "system_name": res.get("SystemName", ""),
        "total_memory_mb": total_mem,
        "assigned_memory_mb": assigned_mem,
        "free_memory_mb": total_mem - assigned_mem,
        "total_proc_units": total_procs,
        "assigned_proc_units": round(assigned_procs, 4),
        "free_proc_units": round(total_procs - assigned_procs, 4),
        "total_lpars": len(lpars),
        "running_lpars": running,
    }


@mcp.tool(annotations=_READ_ONLY)
def hmc_capacity_report() -> list[dict[str, Any]]:
    """Capacity report: for each managed system, total/assigned/free memory (MiB)
    and processor units, plus running and total LPAR counts.

    Derived by listing all managed systems then fetching the LPAR list for each
    system to compute assigned resources. Free = total − assigned.
    """
    async def _go() -> list[dict[str, Any]]:
        async with client_from_env() as hmc:
            systems = await hmc.list_managed_systems()
            result = []
            for system in systems:
                uuid = system.get("UUID")
                lpars = await hmc.list_logical_partitions(uuid) if uuid else []
                result.append(_system_capacity(system, lpars))
            return result

    return _run(_go)


@mcp.tool(annotations=_READ_ONLY)
def hmc_find_placement(
    desired_memory_mb: int,
    desired_proc_units: float = 0.5,
) -> list[dict[str, Any]]:
    """Find managed systems that can host a new LPAR of the given size.

    Returns systems with at least *desired_memory_mb* MiB free and at least
    *desired_proc_units* free processor units, sorted by free memory descending.
    Each result has the same fields as :func:`hmc_capacity_report`.
    """
    async def _go() -> list[dict[str, Any]]:
        async with client_from_env() as hmc:
            systems = await hmc.list_managed_systems()
            candidates = []
            for system in systems:
                uuid = system.get("UUID")
                lpars = await hmc.list_logical_partitions(uuid) if uuid else []
                cap = _system_capacity(system, lpars)
                if (
                    cap["free_memory_mb"] >= desired_memory_mb
                    and cap["free_proc_units"] >= desired_proc_units
                ):
                    candidates.append(cap)
            candidates.sort(key=lambda c: c["free_memory_mb"], reverse=True)
            return candidates

    return _run(_go)
@mcp.tool(annotations=_READ_ONLY)
def hmc_find_system(name: str) -> dict[str, Any] | None:
    """Find a managed system by its SystemName (exact match).

    Returns the full system dict if found, or None if no system with that
    name is known to the HMC.
    """
    return with_client(lambda hmc: hmc.find_system_by_name(name))


@mcp.tool(annotations=_READ_ONLY)
def hmc_wait_for_job(
    job_uuid: str,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
) -> dict[str, Any] | None:
    """Poll an HMC job until it reaches a terminal state (COMPLETED / FAILED / EXCEPTION).

    Returns the final job entry. If *timeout_seconds* elapses before a
    terminal state is reached, returns the last-seen entry regardless of
    status — check the Status field to distinguish timeout from completion.
    """
    return with_client(
        lambda hmc: hmc.wait_for_job(job_uuid, timeout_seconds, poll_interval)
    )
