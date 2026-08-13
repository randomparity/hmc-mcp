"""MCP tools for LPAR/VIOS lifecycle and power control.
"""

from __future__ import annotations

from typing import Any

from ._app import (
    _DESTRUCTIVE,
    _resolve_lpar_uuid,
    _resolve_system_uuid,
    _resolve_vios_uuid,
    _run,
    mcp,
)

from .client import HMCError
from .common import client_from_env
from .documents import (
    Keylock,
    LparResources,
    MemoryMirroringMode,
    OsType,
    PartitionType,
    PowerOffPolicy,
    PowerOnLparStartPolicy,
    build_dlpar_mem_document,
    build_dlpar_proc_document,
    build_lpar_document,
    build_managed_system_document,
)
from .jobs import power_off_lpar_job, power_on_lpar_job


def _check_lpar_write_error(exc: HMCError) -> None:
    """Re-raise *exc* with an actionable message for known LPAR write HTTP errors.

    HTTP 406 on a UOM write (PUT LogicalPartition / POST LogicalPartition) means
    the HMC rejected the request due to a header or XML schema mismatch — the most
    common causes are a wrong or missing Accept/Content-Type media type or a schema
    version the HMC does not recognise.

    All other errors are left unchanged.

    The replacement HMCError intentionally does not forward ``body=exc.body``:
    the constructor would append the parsed HMC body text after the actionable
    message, degrading readability. ``from exc`` sets ``__cause__`` and, combined
    with the implicit ``__context__`` set by the ``except`` block, makes the
    original exception accessible in developer diagnostics.
    """
    if exc.status_code == 406:
        raise HMCError(
            "The HMC rejected the LPAR write request with HTTP 406 "
            "(Not Acceptable). Likely causes: (1) Accept or Content-Type "
            "header mismatch — the HMC may require a more specific media type; "
            "(2) XML schema version mismatch — try setting "
            "HMC_SCHEMA_VERSION=V1_0 in the environment and retrying.",
            exc.status_code,
        ) from exc



@mcp.tool
def hmc_create_lpar(
    system_name_or_uuid: str,
    name: str,
    partition_type: PartitionType = "AIX/Linux",
    partition_id: int | None = None,
    min_memory: int = 256,
    desired_memory: int = 4096,
    max_memory: int = 8192,
    dedicated: bool = False,
    min_procs: float | None = None,
    desired_procs: float | None = None,
    max_procs: float | None = None,
    min_vcpus: int | None = None,
    desired_vcpus: int | None = 1,
    max_vcpus: int | None = 2,
    uncapped: bool = True,
    os_type: OsType | None = None,
    keylock: Keylock | None = None,
    max_virtual_slots: int | None = None,
) -> dict[str, Any] | None:
    """Create a new LPAR on a managed system.

    system_name_or_uuid: the target managed system — accepts either a
    SystemName (e.g. ``"Server-9080-M9S-SN12345"``) or a UUID (find it
    with hmc_systems). Memory values are in MiB. By default a
    shared-processor partition is created; set dedicated=True for dedicated
    CPUs (then procs are whole CPU counts). For shared partitions, procs are
    processing units (may be fractional, e.g. 0.5) and vcpus are virtual
    processor counts.

    The partition is created powered off with a default profile; storage,
    network and boot settings still need to be configured (via the HMC UI or
    profile edits) before it can boot an OS. This creates a real partition —
    confirm name/system_name_or_uuid before calling.

    Raises ValueError if a partition with the given name already exists on
    any managed system — names must be unique across the HMC.

    partition_type must be one of: 'AIX/Linux', 'OS400', 'Virtual IO Server'.
    os_type: target OS — ``aix``, ``linux``, or ``ibmi``.
    keylock: initial keylock position — ``normal``, ``manual``, or ``auto``.
    max_virtual_slots: maximum number of virtual I/O slots.
    """
    xml = build_lpar_document(
        name=name,
        partition_type=partition_type,
        partition_id=partition_id,
        resources=LparResources(
            min_memory=min_memory,
            desired_memory=desired_memory,
            max_memory=max_memory,
            dedicated=dedicated,
            min_procs=min_procs,
            desired_procs=desired_procs,
            max_procs=max_procs,
            min_vcpus=min_vcpus,
            desired_vcpus=desired_vcpus,
            max_vcpus=max_vcpus,
            uncapped=uncapped,
        ),
        os_type=os_type,
        keylock=keylock,
        max_virtual_slots=max_virtual_slots,
    )

    async def _go():
        async with client_from_env() as hmc:
            existing = await hmc.find_partition_by_name(name)
            if existing:
                raise ValueError(
                    f"An LPAR named {name!r} already exists "
                    f"(UUID {existing.get('UUID')!r}). Choose a different name "
                    "or delete the existing partition first."
                )
            system_uuid = await _resolve_system_uuid(hmc, system_name_or_uuid)
            try:
                return await hmc.create_logical_partition(system_uuid, xml)
            except HMCError as exc:
                _check_lpar_write_error(exc)
                raise

    return _run(_go)


@mcp.tool
def hmc_modify_lpar(
    lpar_name_or_uuid: str,
    name: str | None = None,
    min_memory: int | None = None,
    desired_memory: int | None = None,
    max_memory: int | None = None,
    dedicated: bool | None = None,
    min_procs: float | None = None,
    desired_procs: float | None = None,
    max_procs: float | None = None,
    min_vcpus: int | None = None,
    desired_vcpus: int | None = None,
    max_vcpus: int | None = None,
    uncapped: bool | None = None,
) -> dict[str, Any] | None:
    """Modify an LPAR's name and/or resource assignment (memory / CPU).

    lpar_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_lpars). Only the fields you pass are changed.
    Memory values are in MiB. For a running partition these are dynamic
    (DLPAR) operations and require an active RMC connection; otherwise the
    change applies on next activation. Set dedicated=True to assign whole
    CPUs, False for shared processing units + virtual processors; omit it
    to leave the sharing mode unchanged.
    """
    xml = build_lpar_document(
        name=name,
        resources=LparResources(
            min_memory=min_memory,
            desired_memory=desired_memory,
            max_memory=max_memory,
            dedicated=dedicated,
            min_procs=min_procs,
            desired_procs=desired_procs,
            max_procs=max_procs,
            min_vcpus=min_vcpus,
            desired_vcpus=desired_vcpus,
            max_vcpus=max_vcpus,
            uncapped=uncapped,
        ),
    )

    async def _go():
        async with client_from_env() as hmc:
            lpar_uuid = await _resolve_lpar_uuid(hmc, lpar_name_or_uuid)
            try:
                return await hmc.modify_logical_partition(lpar_uuid, xml)
            except HMCError as exc:
                _check_lpar_write_error(exc)
                raise

    return _run(_go)


@mcp.tool
def hmc_dlpar_proc(
    lpar_name_or_uuid: str,
    desired_procs: float | None = None,
    min_procs: float | None = None,
    max_procs: float | None = None,
    desired_vcpus: int | None = None,
    min_vcpus: int | None = None,
    max_vcpus: int | None = None,
    dedicated: bool | None = None,
    uncapped: bool | None = None,
) -> dict[str, Any] | None:
    """DLPAR processor hot-plug: change CPU resources on a running LPAR.

    lpar_name_or_uuid: accepts either a PartitionName or a UUID.
    Posts a minimal PartitionProcessorConfiguration document to the HMC.
    Only the fields you pass are changed. For shared partitions, procs are
    processing units (may be fractional, e.g. 0.5); vcpus are virtual
    processor counts (ints). Set dedicated=True for whole-CPU assignment,
    False for shared; omit it to leave the sharing mode unchanged.

    If the LPAR does not have an active RMC connection, the change is
    profile-only and takes effect on next activation (no reboot is triggered).
    """
    xml = build_dlpar_proc_document(
        LparResources(
            desired_procs=desired_procs,
            min_procs=min_procs,
            max_procs=max_procs,
            desired_vcpus=desired_vcpus,
            min_vcpus=min_vcpus,
            max_vcpus=max_vcpus,
            dedicated=dedicated,
            uncapped=uncapped,
        )
    )

    async def _go():
        async with client_from_env() as hmc:
            lpar_uuid = await _resolve_lpar_uuid(hmc, lpar_name_or_uuid)
            return await hmc.modify_logical_partition(lpar_uuid, xml)

    return _run(_go)


@mcp.tool
def hmc_modify_system(
    system_name_or_uuid: str,
    new_name: str | None = None,
    power_off_policy: PowerOffPolicy | None = None,
    power_on_lpar_start_policy: PowerOnLparStartPolicy | None = None,
    pend_mem_region_size: int | None = None,
    requested_num_sys_huge_pages: int | None = None,
    mem_mirroring_mode: MemoryMirroringMode | None = None,
) -> dict[str, Any] | None:
    """Modify a managed system's configuration.

    Only the fields you pass are changed; omitted fields are left as-is.

    system_name_or_uuid: the managed system — accepts either a SystemName or
        a UUID (from hmc_systems).
    new_name: rename the managed system.
    power_off_policy: power-off policy — 1 powers the system off after all
        partitions shut down, 0 leaves it powered on.
    power_on_lpar_start_policy: LPAR auto-start policy on system power-on —
        'autostart', 'userinit', or 'autorecovery'.
    pend_mem_region_size: pending memory region size (MiB).
    requested_num_sys_huge_pages: number of huge memory pages to allocate.
    mem_mirroring_mode: memory mirroring mode — 'none' or 'sys_firmware_only'.
    """
    xml = build_managed_system_document(
        new_name=new_name,
        power_off_policy=power_off_policy,
        power_on_lpar_start_policy=power_on_lpar_start_policy,
        pend_mem_region_size=pend_mem_region_size,
        requested_num_sys_huge_pages=requested_num_sys_huge_pages,
        mem_mirroring_mode=mem_mirroring_mode,
    )

    async def _go():
        async with client_from_env() as hmc:
            system_uuid = await _resolve_system_uuid(hmc, system_name_or_uuid)
            return await hmc.modify_managed_system(system_uuid, xml)

    return _run(_go)


@mcp.tool
def hmc_dlpar_mem(
    lpar_name_or_uuid: str,
    desired_memory: int | None = None,
    min_memory: int | None = None,
    max_memory: int | None = None,
) -> dict[str, Any] | None:
    """DLPAR memory hot-plug: change memory resources on a running LPAR.

    lpar_name_or_uuid: accepts either a PartitionName or a UUID.
    Posts a minimal PartitionMemoryConfiguration document to the HMC.
    Memory values are in MiB. Only the fields you pass are changed.

    If the LPAR does not have an active RMC connection, the change is
    profile-only and takes effect on next activation (no reboot is triggered).
    """
    xml = build_dlpar_mem_document(
        LparResources(
            desired_memory=desired_memory,
            min_memory=min_memory,
            max_memory=max_memory,
        )
    )

    async def _go():
        async with client_from_env() as hmc:
            lpar_uuid = await _resolve_lpar_uuid(hmc, lpar_name_or_uuid)
            return await hmc.modify_logical_partition(lpar_uuid, xml)

    return _run(_go)


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_delete_lpar(lpar_name_or_uuid: str) -> str:
    """Delete (destroy) an LPAR by name or UUID.

    The partition must be powered off first (use hmc_power_off_lpar and
    confirm with hmc_lpars(lpar_name_or_uuid=..., state_only=True)). This
    tool refuses to delete a partition whose current state is anything other
    than 'not activated', matching the precondition check pattern used by
    hmc_remove_memory_pool. This permanently removes the partition and its
    profiles from the HMC — it is irreversible. Confirm the target with
    hmc_lpars(name=...) before calling. Returns a confirmation string
    (immediate delete — no job to poll).

    lpar_name_or_uuid: accepts either a PartitionName or a UUID.

    Raises:
        HMCError: If the partition state is not 'not activated' (HTTP 409).
    """

    async def _go():
        async with client_from_env() as hmc:
            lpar_uuid = await _resolve_lpar_uuid(hmc, lpar_name_or_uuid)
            state = await hmc.get_quick_property(
                "LogicalPartition", lpar_uuid, "PartitionState"
            )
            if state != "not activated":
                raise HMCError(
                    f"Cannot delete LPAR {lpar_uuid} — current state is "
                    f"{state!r}; it must be 'not activated' to delete. Power it "
                    "off (hmc_power_off_lpar) and confirm with "
                    "hmc_lpars(lpar_name_or_uuid=..., state_only=True) before retrying.",
                    status_code=409,
                )
            await hmc.delete_logical_partition(lpar_uuid)
            return f"Deleted LPAR {lpar_uuid}"

    return _run(_go)




def _extract_job_id(job: dict[str, Any]) -> str | None:
    """Extract the job UUID from a submitted job dict."""
    return job.get("UUID") or (job.get("Resource") or {}).get("JobID")


async def _power_op(submit_fn, wait: bool, timeout_seconds: int, poll_interval: int) -> dict[str, Any] | None:
    """Submit a power job; optionally wait for it to reach a terminal state.

    *submit_fn* is ``async (hmc) -> job_entry``.  When *wait* is False the
    submitted job entry is returned immediately (the existing behaviour).
    When *wait* is True the job UUID is extracted from the entry and
    ``wait_for_job`` is called before returning.
    """
    async with client_from_env() as hmc:
        job = await submit_fn(hmc)
        if not wait or job is None:
            return job
        job_uuid = _extract_job_id(job)
        if not job_uuid:
            return job
        return await hmc.wait_for_job(
            job_uuid, timeout_seconds, poll_interval, job_href=job.get("link")
        )


@mcp.tool
def hmc_power_on_lpar(
    lpar_name_or_uuid: str,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    force: bool = False,
) -> dict[str, Any] | None:
    """Submit a PowerOn job for a logical partition.

    lpar_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_lpars). Returns the submitted job (check hmc_get_job
    for status). This changes the state of a real partition — confirm the
    target with hmc_lpars(name=...) before calling.

    If the partition is already in the 'running' state, the tool returns
    ``{"already_running": True, "message": "..."}`` without submitting a job.
    Pass force=True to skip this check and submit the PowerOn unconditionally.

    Set wait=True to block until the job reaches COMPLETED / FAILED / EXCEPTION
    (or until timeout_seconds elapses).
    """
    async def _go():
        async with client_from_env() as hmc:
            lpar_uuid = await _resolve_lpar_uuid(hmc, lpar_name_or_uuid)
            if not force:
                state = await hmc.get_quick_property(
                    "LogicalPartition", lpar_uuid, "PartitionState"
                )
                if state == "running":
                    return {
                        "already_running": True,
                        "message": (
                            f"LPAR {lpar_uuid} is already running. "
                            "Use force=True to submit PowerOn anyway."
                        ),
                    }
            job = await hmc.submit_job(
                f"/rest/api/uom/LogicalPartition/{lpar_uuid}/do/PowerOn",
                power_on_lpar_job(),
            )
            if not wait or job is None:
                return job
            job_uuid = _extract_job_id(job)
            return await hmc.wait_for_job(job_uuid, timeout_seconds, poll_interval, job_href=job.get("link")) if job_uuid else job

    return _run(_go)


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_power_off_lpar(
    lpar_name_or_uuid: str,
    immediate: bool = False,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
) -> dict[str, Any] | None:
    """Submit a PowerOff job for a logical partition.

    lpar_name_or_uuid: accepts either a PartitionName or a UUID.
    immediate=True forces an immediate power off (no graceful OS shutdown).
    Returns the submitted job. This changes the state of a real partition.

    Set wait=True to block until the job reaches a terminal state.
    """
    async def _go():
        async with client_from_env() as hmc:
            lpar_uuid = await _resolve_lpar_uuid(hmc, lpar_name_or_uuid)
            job = await hmc.submit_job(
                f"/rest/api/uom/LogicalPartition/{lpar_uuid}/do/PowerOff",
                power_off_lpar_job(immediate=immediate),
            )
            if not wait or job is None:
                return job
            job_uuid = _extract_job_id(job)
            return await hmc.wait_for_job(job_uuid, timeout_seconds, poll_interval, job_href=job.get("link")) if job_uuid else job

    return _run(_go)


@mcp.tool
def hmc_power_on_system(
    system_name_or_uuid: str,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
) -> dict[str, Any] | None:
    """Power on a managed system (PowerOn job).

    system_name_or_uuid: accepts either a SystemName or a UUID
    (find it with hmc_systems).
    Set wait=True to block until the job reaches a terminal state.
    """
    async def _go():
        async with client_from_env() as hmc:
            system_uuid = await _resolve_system_uuid(hmc, system_name_or_uuid)
            job = await hmc.power_on_system(system_uuid)
            if not wait or job is None:
                return job
            job_uuid = _extract_job_id(job)
            return await hmc.wait_for_job(job_uuid, timeout_seconds, poll_interval, job_href=job.get("link")) if job_uuid else job

    return _run(_go)


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_power_off_system(
    system_name_or_uuid: str,
    immediate: bool = False,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
) -> dict[str, Any] | None:
    """Power off a managed system (PowerOff job). immediate skips graceful shutdown.

    system_name_or_uuid: accepts either a SystemName or a UUID.
    Set wait=True to block until the job reaches a terminal state.
    """
    async def _go():
        async with client_from_env() as hmc:
            system_uuid = await _resolve_system_uuid(hmc, system_name_or_uuid)
            job = await hmc.power_off_system(system_uuid, immediate)
            if not wait or job is None:
                return job
            job_uuid = _extract_job_id(job)
            return await hmc.wait_for_job(job_uuid, timeout_seconds, poll_interval, job_href=job.get("link")) if job_uuid else job

    return _run(_go)


@mcp.tool
def hmc_power_on_vios(
    vios_name_or_uuid: str,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
) -> dict[str, Any] | None:
    """Power on a VIOS (PowerOn job).

    vios_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_vios).
    Set wait=True to block until the job reaches a terminal state.
    """
    async def _go():
        async with client_from_env() as hmc:
            vios_uuid = await _resolve_vios_uuid(hmc, vios_name_or_uuid)
            job = await hmc.power_on_vios(vios_uuid)
            if not wait or job is None:
                return job
            job_uuid = _extract_job_id(job)
            return await hmc.wait_for_job(job_uuid, timeout_seconds, poll_interval, job_href=job.get("link")) if job_uuid else job

    return _run(_go)


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_power_off_vios(
    vios_name_or_uuid: str,
    immediate: bool = False,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
) -> dict[str, Any] | None:
    """Power off a VIOS (PowerOff job). immediate skips graceful shutdown.

    vios_name_or_uuid: accepts either a PartitionName or a UUID.
    Set wait=True to block until the job reaches a terminal state.
    """
    async def _go():
        async with client_from_env() as hmc:
            vios_uuid = await _resolve_vios_uuid(hmc, vios_name_or_uuid)
            job = await hmc.power_off_vios(vios_uuid, immediate)
            if not wait or job is None:
                return job
            job_uuid = _extract_job_id(job)
            return await hmc.wait_for_job(job_uuid, timeout_seconds, poll_interval, job_href=job.get("link")) if job_uuid else job

    return _run(_go)


