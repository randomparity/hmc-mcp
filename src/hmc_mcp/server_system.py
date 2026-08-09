"""MCP tools for read-only inventory and job status, plus the HMC CLI escape hatch.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ._app import (
    _READ_ONLY,
    _run,
    mcp,
    with_client,
)

from .common import client_from_env
from .ssh import run_hmc_cli


def _int_field(resource: dict[str, Any], *keys: str, default: int = 0) -> int:
    """Extract an integer from a parsed Resource dict, trying each key in order."""
    for key in keys:
        val = resource.get(key)
        if val is not None:
            try:
                return int(float(str(val)))
            except (TypeError, ValueError):
                pass
    return default


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
def hmc_systems(
    system_uuid: str | None = None,
    name: str | None = None,
) -> list[dict[str, Any]] | dict[str, Any] | None:
    """List or look up managed systems (Power servers).

    - No arguments: list all managed systems known to the HMC.
    - *system_uuid*: return full details for one system by UUID.
    - *name*: find a system by its SystemName (exact match), return the first
      match or None.

    Returns one entry per system when listing, or a single dict when a
    *system_uuid* or *name* is supplied.  Each entry contains UUID,
    SystemName, State, MTMS (machine type/model/serial), IPAddress, etc.
    """
    if system_uuid is not None:
        return with_client(lambda hmc: hmc.get_managed_system(system_uuid))
    if name is not None:
        return with_client(lambda hmc: hmc.find_system_by_name(name))
    return with_client(lambda hmc: hmc.list_managed_systems())


@mcp.tool(annotations=_READ_ONLY)
def hmc_lpars(
    system_uuid: str | None = None,
    lpar_uuid: str | None = None,
    name: str | None = None,
    state_only: bool = False,
) -> list[dict[str, Any]] | dict[str, Any] | str | None:
    """List or look up logical partitions (LPARs).

    - No arguments: list every LPAR known to the HMC.
    - *system_uuid* only: list LPARs on that managed system.
    - *lpar_uuid*: return full details for one LPAR by UUID.
    - *name*: find an LPAR by its PartitionName (exact match), return the
      first match or None.
    - *state_only=True* (requires *lpar_uuid*): return just the current
      PartitionState string using the cheap quick-property endpoint.
    """
    if state_only:
        if lpar_uuid is None:
            raise ValueError("lpar_uuid is required when state_only=True")
        return with_client(
            lambda hmc: hmc.get_quick_property("LogicalPartition", lpar_uuid, "PartitionState")
        )
    if lpar_uuid is not None:
        return with_client(lambda hmc: hmc.get_logical_partition(lpar_uuid))
    if name is not None:
        return with_client(lambda hmc: hmc.find_partition_by_name(name))
    return with_client(lambda hmc: hmc.list_logical_partitions(system_uuid))


@mcp.tool(annotations=_READ_ONLY)
def hmc_vios(
    system_uuid: str | None = None,
    vios_uuid: str | None = None,
) -> list[dict[str, Any]] | dict[str, Any] | None:
    """List Virtual I/O Servers or return storage mapping facts for one VIOS.

    - No arguments: list all VIOSes known to the HMC.
    - *system_uuid* only: list VIOSes on that managed system.
    - *vios_uuid*: return VIOS device mapping facts (ViosStorageDetail group),
      which contains VirtualSCSIMappings (physical volumes and virtual disks
      served to LPARs) and VirtualFibreChannelMappings (NPIV port mappings).
      Equivalent to Ansible ``vios_mapping_facts``.
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


@mcp.tool(annotations=_READ_ONLY)
def hmc_wait_for_job(
    job_uuid: str,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
) -> dict[str, Any] | None:
    """Poll an HMC job until it reaches a terminal status or the timeout expires.

    Terminal statuses: ``COMPLETED``, ``FAILED``, ``EXCEPTION``.
    Returns the final job entry. If the timeout fires before a terminal status
    is reached, returns the last-seen entry (check the ``Status`` field).

    Use after submitting a job with a power/install/update tool when you want
    to block until the operation completes rather than polling manually.

    Args:
        job_uuid: UUID of the job returned by the submitting tool.
        timeout_seconds: Maximum seconds to wait (default 300).
        poll_interval: Seconds between polls (default 5).
    """

    return with_client(
        lambda hmc: hmc.wait_for_job(job_uuid, timeout_seconds, poll_interval)
    )


@mcp.tool(annotations=_READ_ONLY)
def hmc_recent_jobs(limit: int = 20) -> list[dict[str, Any]]:
    """List recent HMC jobs (audit view of recent activity).

    Returns up to *limit* jobs from the HMC's job log, most-recent first.
    Each entry includes the job UUID, operation type, state, status, and
    timestamps so you can review what actions were submitted recently.

    Useful for auditing or for recovering a job UUID when the original
    request response was lost.
    """

    jobs = with_client(lambda hmc: hmc.list_uom("Job"))
    return jobs[:limit] if limit > 0 else jobs


@mcp.tool(annotations=_READ_ONLY)
def hmc_capacity_report() -> list[dict[str, Any]]:
    """Report per-managed-system memory and CPU capacity.

    For each managed system returns:

    - ``system_uuid``: UUID of the managed system.
    - ``system_name``: Name of the managed system.
    - ``state``: Current system state.
    - ``total_memory_mb``: Total installed memory (MiB).
    - ``assigned_memory_mb``: Sum of DesiredMemory across all LPARs on the system.
    - ``free_memory_mb``: Estimated free memory (total - assigned).
    - ``total_proc_units``: Total installed processing units.
    - ``assigned_proc_units``: Sum of DesiredProcessingUnits / DesiredProcessors
      across all LPARs on the system.
    - ``running_lpars``: Count of LPARs in the ``running`` state.
    - ``total_lpars``: Total LPAR count on the system.

    Note: capacity values are derived from LPAR desired-resource totals and the
    system's installed capacity; they are a best-effort estimate. Some fields
    may be absent for older HMC firmware or specific partition types.
    """

    async def _go():
        async with client_from_env() as hmc:
            systems = await hmc.list_managed_systems()
            report = []
            for sys in systems:
                uuid = sys.get("UUID", "")
                res = sys.get("Resource", {})
                system_name = res.get("SystemName", uuid)
                state = res.get("State", "unknown")

                total_mem = _int_field(res, "InstalledSystemMemory", "TotalSystemMemory")
                total_procs_raw = res.get("InstalledSystemProcessorUnits") or res.get(
                    "TotalSystemProcessorUnits"
                )
                if total_procs_raw is not None:
                    try:
                        total_procs = float(str(total_procs_raw)) / 100.0
                    except (TypeError, ValueError):
                        total_procs = 0.0
                else:
                    total_procs = 0.0

                lpars = await hmc.list_logical_partitions(uuid) if uuid else []
                assigned_mem = 0
                assigned_procs = 0.0
                running = 0
                for lp in lpars:
                    lr = lp.get("Resource", {})
                    assigned_mem += _int_field(lr, "DesiredMemory")
                    raw = lr.get("DesiredProcessingUnits") or lr.get("DesiredProcessors")
                    if raw is not None:
                        try:
                            v = float(str(raw))
                            # DesiredProcessingUnits is stored *100 on some HMC versions
                            assigned_procs += v / 100.0 if v > 10 else v
                        except (TypeError, ValueError):
                            pass
                    if lr.get("PartitionState") == "running":
                        running += 1

                report.append({
                    "system_uuid": uuid,
                    "system_name": system_name,
                    "state": state,
                    "total_memory_mb": total_mem,
                    "assigned_memory_mb": assigned_mem,
                    "free_memory_mb": max(0, total_mem - assigned_mem),
                    "total_proc_units": round(total_procs, 2),
                    "assigned_proc_units": round(assigned_procs, 2),
                    "running_lpars": running,
                    "total_lpars": len(lpars),
                })
            return report

    return asyncio.run(_go())


@mcp.tool(annotations=_READ_ONLY)
def hmc_find_placement(
    desired_memory_mb: int,
    desired_proc_units: float = 0.5,
) -> list[dict[str, Any]]:
    """Find managed systems that can host an LPAR of the given size.

    Checks each managed system's free memory and CPU and returns those where
    both ``free_memory_mb >= desired_memory_mb`` and
    ``(total_proc_units - assigned_proc_units) >= desired_proc_units``.

    Args:
        desired_memory_mb: Required memory in MiB (e.g. 4096 for 4 GiB).
        desired_proc_units: Required processing units (default 0.5). Use an
            integer value for dedicated-processor partitions.

    Returns a list of candidate systems — each entry is the same dict as
    ``hmc_capacity_report`` — sorted by free memory descending.
    """

    async def _go():
        async with client_from_env() as hmc:
            systems = await hmc.list_managed_systems()
            candidates = []
            for sys in systems:
                uuid = sys.get("UUID", "")
                res = sys.get("Resource", {})
                system_name = res.get("SystemName", uuid)
                state = res.get("State", "unknown")

                total_mem = _int_field(res, "InstalledSystemMemory", "TotalSystemMemory")
                total_procs_raw = res.get("InstalledSystemProcessorUnits") or res.get(
                    "TotalSystemProcessorUnits"
                )
                if total_procs_raw is not None:
                    try:
                        total_procs = float(str(total_procs_raw)) / 100.0
                    except (TypeError, ValueError):
                        total_procs = 0.0
                else:
                    total_procs = 0.0

                lpars = await hmc.list_logical_partitions(uuid) if uuid else []
                assigned_mem = 0
                assigned_procs = 0.0
                running = 0
                for lp in lpars:
                    lr = lp.get("Resource", {})
                    assigned_mem += _int_field(lr, "DesiredMemory")
                    raw = lr.get("DesiredProcessingUnits") or lr.get("DesiredProcessors")
                    if raw is not None:
                        try:
                            v = float(str(raw))
                            assigned_procs += v / 100.0 if v > 10 else v
                        except (TypeError, ValueError):
                            pass
                    if lr.get("PartitionState") == "running":
                        running += 1

                free_mem = max(0, total_mem - assigned_mem)
                free_procs = max(0.0, total_procs - assigned_procs)

                if free_mem >= desired_memory_mb and free_procs >= desired_proc_units:
                    candidates.append({
                        "system_uuid": uuid,
                        "system_name": system_name,
                        "state": state,
                        "total_memory_mb": total_mem,
                        "assigned_memory_mb": assigned_mem,
                        "free_memory_mb": free_mem,
                        "total_proc_units": round(total_procs, 2),
                        "assigned_proc_units": round(assigned_procs, 2),
                        "running_lpars": running,
                        "total_lpars": len(lpars),
                    })

            candidates.sort(key=lambda x: x["free_memory_mb"], reverse=True)
            return candidates

    return asyncio.run(_go())
