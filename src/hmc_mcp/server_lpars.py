"""MCP tools for LPAR creation, mutation, deletion, and power control."""

from __future__ import annotations

from typing import Any

from ._app import (
    _DESTRUCTIVE,
    _run,
    mcp,
)
from .errors import HMCError
from .common import (
    client_from_env,
    resolve_lpar_uuid,
    resolve_system_uuid,
)
from .documents import (
    Keylock,
    LparResources,
    OsType,
    PartitionType,
    build_dlpar_mem_document,
    build_dlpar_proc_document,
    build_lpar_document,
)
from .jobs import power_off_lpar_job, power_on_lpar_job, wait_for_submitted_job
from .operations_lpar import (
    LparCreation,
    LparCreationResult,
    authorize_lpar_mutation,
    create_and_stamp_lpar,
    resolve_lpar_ownership_names,
)


def _check_lpar_write_error(exc: HMCError) -> None:
    """Translate LPAR write rejection without its response body."""
    if exc.status_code == 406:
        raise HMCError(
            "The HMC rejected the LPAR write request (Not Acceptable). "
            "Likely causes: (1) Accept or Content-Type header mismatch — "
            "the HMC may require a more specific media type; "
            "(2) XML schema version mismatch — try setting "
            "HMC_SCHEMA_VERSION=V1_0 in the environment and retrying.",
            exc.status_code,
        ) from exc


@mcp.tool
def hmc_create_lpar(
    system_name_or_uuid: str,
    name: str,
    resources: LparResources = LparResources(
        min_memory=256,
        desired_memory=4096,
        max_memory=8192,
        dedicated=False,
        desired_vcpus=1,
        max_vcpus=2,
        uncapped=True,
    ),
    partition_type: PartitionType = "AIX/Linux",
    partition_id: int | None = None,
    os_type: OsType | None = None,
    keylock: Keylock | None = None,
    max_virtual_slots: int | None = None,
    profile: str | None = None,
) -> LparCreationResult:
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

    Returns a dict with the following keys:

    - ``lpar`` — the created partition entry (dict), or ``None`` when the HMC
      returned no body (HTTP 201 with empty body, seen on some firmware versions).
    - ``ownership_stamped`` — ``True`` when the description-field ownership token
      was written; ``False`` when the SSH stamp attempt failed; ``None`` when the
      stamp was not attempted (no LPAR body available to confirm the partition name).
    - ``warnings`` — list of human-readable warning strings (empty on clean success).
    """
    xml = build_lpar_document(
        name=name,
        partition_type=partition_type,
        partition_id=partition_id,
        resources=resources,
        os_type=os_type,
        keylock=keylock,
        max_virtual_slots=max_virtual_slots,
    )

    async def _go():
        async with client_from_env(profile) as hmc:
            existing = await hmc.find_partition_by_name(name)
            if existing:
                raise ValueError(
                    f"An LPAR named {name!r} already exists "
                    f"(UUID {existing.get('UUID')!r}). Choose a different name "
                    "or delete the existing partition first."
                )

            system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
            try:
                return await create_and_stamp_lpar(
                    hmc,
                    system_uuid,
                    system_name_or_uuid,
                    LparCreation(name, partition_type, resources, max_virtual_slots),
                    xml,
                )
            except HMCError as exc:
                _check_lpar_write_error(exc)
                raise

    return _run(_go)


@mcp.tool
def hmc_modify_lpar(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    name: str | None = None,
    resources: LparResources = LparResources(),
    ownership_override: bool = False,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Modify an LPAR's name and/or resource assignment (memory / CPU).

    lpar_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_lpars). Only the fields you pass are changed.
    Memory values are in MiB. For a running partition these are dynamic
    (DLPAR) operations and require an active RMC connection; otherwise the
    change applies on next activation. Set dedicated=True to assign whole
    CPUs, False for shared processing units + virtual processors; omit it
    to leave the sharing mode unchanged.

    Renaming enforces the description-field ownership token. Foreign-owned or
    malformed tokens are rejected before the REST write. Set
    ownership_override=True only after explicit operator approval.
    """
    xml = build_lpar_document(
        name=name,
        resources=resources,
    )

    async def _go():
        async with client_from_env(profile) as hmc:
            lpar_uuid = await resolve_lpar_uuid(hmc, lpar_name_or_uuid)
            if name is not None and not ownership_override:
                system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
                system_name, lpar_name = await resolve_lpar_ownership_names(
                    hmc, system_uuid, system_name_or_uuid, lpar_uuid
                )
                await authorize_lpar_mutation(
                    hmc,
                    system_name,
                    lpar_name,
                    ownership_override=ownership_override,
                )
            try:
                return await hmc.modify_logical_partition(lpar_uuid, xml)
            except HMCError as exc:
                _check_lpar_write_error(exc)
                raise

    return _run(_go)


@mcp.tool
def hmc_dlpar_proc(
    lpar_name_or_uuid: str,
    resources: LparResources = LparResources(),
    profile: str | None = None,
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
    xml = build_dlpar_proc_document(resources)

    async def _go():
        async with client_from_env(profile) as hmc:
            lpar_uuid = await resolve_lpar_uuid(hmc, lpar_name_or_uuid)
            try:
                return await hmc.modify_logical_partition(lpar_uuid, xml)
            except HMCError as exc:
                _check_lpar_write_error(exc)
                raise

    return _run(_go)


@mcp.tool
def hmc_dlpar_mem(
    lpar_name_or_uuid: str,
    resources: LparResources = LparResources(),
    profile: str | None = None,
) -> dict[str, Any] | None:
    """DLPAR memory hot-plug: change memory resources on a running LPAR.

    lpar_name_or_uuid: accepts either a PartitionName or a UUID.
    Posts a minimal PartitionMemoryConfiguration document to the HMC.
    Memory values are in MiB. Only the fields you pass are changed.

    If the LPAR does not have an active RMC connection, the change is
    profile-only and takes effect on next activation (no reboot is triggered).
    """
    xml = build_dlpar_mem_document(resources)

    async def _go():
        async with client_from_env(profile) as hmc:
            lpar_uuid = await resolve_lpar_uuid(hmc, lpar_name_or_uuid)
            try:
                return await hmc.modify_logical_partition(lpar_uuid, xml)
            except HMCError as exc:
                _check_lpar_write_error(exc)
                raise

    return _run(_go)


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_delete_lpar(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    ownership_override: bool = False,
    profile: str | None = None,
) -> str:
    """Delete (destroy) an LPAR by name or UUID.

    The partition must be powered off first (use hmc_power_off_lpar and
    confirm with hmc_get_lpar_state). This
    tool refuses to delete a partition whose current state is anything other
    than 'not activated', matching the precondition check pattern used by
    hmc_remove_memory_pool. This permanently removes the partition and its
    profiles from the HMC — it is irreversible. Confirm the target with
    hmc_get_lpar(lpar_name_or_uuid=...) before calling. Returns a confirmation string
    (immediate delete — no job to poll).

    lpar_name_or_uuid: accepts either a PartitionName or a UUID.

    Deletion enforces the description-field ownership token. Foreign-owned or
    malformed tokens are rejected before state checks or deletion. Set
    ownership_override=True only after explicit operator approval.

    Raises:
        HMCError: If the partition state is not 'not activated' (HTTP 409).
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            lpar_uuid = await resolve_lpar_uuid(hmc, lpar_name_or_uuid)
            if not ownership_override:
                system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
                system_name, lpar_name = await resolve_lpar_ownership_names(
                    hmc, system_uuid, system_name_or_uuid, lpar_uuid
                )
                await authorize_lpar_mutation(hmc, system_name, lpar_name)
            state = await hmc.get_quick_property(
                "LogicalPartition", lpar_uuid, "PartitionState"
            )
            if state != "not activated":
                raise HMCError(
                    f"Cannot delete LPAR {lpar_uuid} — current state is "
                    f"{state!r}; it must be 'not activated' to delete. Power it "
                    "off (hmc_power_off_lpar) and confirm with "
                    "hmc_get_lpar_state before retrying.",
                    status_code=409,
                )
            await hmc.delete_logical_partition(lpar_uuid)
            return f"Deleted LPAR {lpar_uuid}"

    return _run(_go)


async def _power_op(
    hmc, submit_fn, wait: bool, timeout_seconds: int, poll_interval: int
) -> dict[str, Any] | None:
    """Submit a power job on an already-open *hmc* client; optionally wait for terminal state."""
    job = await submit_fn(hmc)
    return await wait_for_submitted_job(hmc, job, wait, timeout_seconds, poll_interval)


@mcp.tool
def hmc_power_on_lpar(
    lpar_name_or_uuid: str,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    force: bool = False,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Submit a PowerOn job for a logical partition.

    lpar_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_lpars). Returns the submitted job (check hmc_get_job
    for status). This changes the state of a real partition — confirm the
    target with hmc_get_lpar(lpar_name_or_uuid=...) before calling.

    If the partition is already in the 'running' state, the tool returns
    ``{"already_running": True, "message": "..."}`` without submitting a job.
    Pass force=True to skip this check and submit the PowerOn unconditionally.

    Set wait=True to block until the job reaches COMPLETED / FAILED / EXCEPTION
    (or until timeout_seconds elapses).
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            lpar_uuid = await resolve_lpar_uuid(hmc, lpar_name_or_uuid)
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
            return await _power_op(
                hmc,
                lambda client: client.submit_job(
                    f"/rest/api/uom/LogicalPartition/{lpar_uuid}/do/PowerOn",
                    power_on_lpar_job(),
                ),
                wait,
                timeout_seconds,
                poll_interval,
            )

    return _run(_go)


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_power_off_lpar(
    lpar_name_or_uuid: str,
    immediate: bool = False,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Submit a PowerOff job for a logical partition.

    lpar_name_or_uuid: accepts either a PartitionName or a UUID.
    immediate=True forces an immediate power off (no graceful OS shutdown).
    Returns the submitted job. This changes the state of a real partition.

    Set wait=True to block until the job reaches a terminal state.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            lpar_uuid = await resolve_lpar_uuid(hmc, lpar_name_or_uuid)
            return await _power_op(
                hmc,
                lambda client: client.submit_job(
                    f"/rest/api/uom/LogicalPartition/{lpar_uuid}/do/PowerOff",
                    power_off_lpar_job(immediate=immediate),
                ),
                wait,
                timeout_seconds,
                poll_interval,
            )

    return _run(_go)
