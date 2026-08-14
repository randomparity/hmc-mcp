"""MCP tools for LPAR creation, mutation, deletion, and power control."""

from __future__ import annotations

from .tool_registry import tool_module

from typing import Any

from ._app import (
    _DESTRUCTIVE,
    _run,
)
from .errors import HMCError
from .common import client_from_env, resolve_lpar_uuid
from .documents import (
    Keylock,
    LparResources,
    OsType,
    PartitionType,
    build_dlpar_mem_document,
    build_dlpar_proc_document,
    build_lpar_document,
)
from .jobs import validate_wait_timing
from .operations_lpar import (
    LparCreation,
    LparPowerOnOutcome,
    LparCreationResult,
    create_and_stamp_lpar,
    delete_lpar,
    power_lpar,
    power_on_outcome,
    rename_lpar,
)


def _check_lpar_write_error(exc: HMCError) -> None:
    """Translate an LPAR write rejection while preserving its response body."""
    if exc.status_code == 406:
        raise HMCError(
            "The HMC rejected the LPAR write request (Not Acceptable). "
            "Likely causes: (1) Accept or Content-Type header mismatch — "
            "the HMC may require a more specific media type; "
            "(2) XML schema version mismatch — try setting "
            "HMC_SCHEMA_VERSION=V1_0 in the environment and retrying.",
            exc.status_code,
            body=exc.body,
        ) from exc


tool, register_tools = tool_module()


@tool
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
    with hmc_list_systems). Memory values are in MiB. By default a
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

    Args:
        system_name_or_uuid: SystemName or UUID of the managed system to create on.
        name: Unique PartitionName for the new logical partition.
        resources: Memory and processor assignments for the new partition.
        partition_type: Partition type: AIX/Linux, OS400, or Virtual IO Server.
        partition_id: Optional numeric partition ID; the HMC assigns one when omitted.
        os_type: Optional target operating-system family: aix, linux, or ibmi.
        keylock: Optional initial keylock position: normal, manual, or auto.
        max_virtual_slots: Optional maximum number of virtual I/O slots.
        profile: Optional configured HMC profile name; uses the default when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            try:
                return await create_and_stamp_lpar(
                    hmc,
                    system_name_or_uuid,
                    LparCreation(
                        name,
                        partition_type,
                        resources,
                        partition_id,
                        os_type,
                        keylock,
                        max_virtual_slots,
                    ),
                )
            except HMCError as exc:
                _check_lpar_write_error(exc)
                raise

    return _run(_go)


@tool
def hmc_modify_lpar(
    lpar_name_or_uuid: str,
    resources: LparResources = LparResources(),
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Modify an LPAR's memory or CPU resource assignment.

    lpar_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_list_lpars). Only the fields you pass are changed.
    Memory values are in MiB. For a running partition these are dynamic
    (DLPAR) operations and require an active RMC connection; otherwise the
    change applies on next activation. Set dedicated=True to assign whole
    CPUs, False for shared processing units + virtual processors; omit it
    to leave the sharing mode unchanged.

    Use hmc_rename_lpar for a name change, which requires a managed-system
    selector for ownership authorization.

    Before modifying, inspect the description with hmc_get_lpar_description.
    Under the ADR 0011 advisory protocol, stop and ask the operator when its
    ownership token names a different agent.

    Args:
        lpar_name_or_uuid: PartitionName or UUID of the logical partition to modify.
        resources: Memory and processor fields to change; omitted fields stay unchanged.
        profile: Optional configured HMC profile name; uses the default when omitted.
    """
    xml = build_lpar_document(name=None, resources=resources)

    async def _go():
        async with client_from_env(profile) as hmc:
            lpar_uuid = await resolve_lpar_uuid(hmc, lpar_name_or_uuid)
            try:
                return await hmc.modify_logical_partition(lpar_uuid, xml)
            except HMCError as exc:
                _check_lpar_write_error(exc)
                raise

    return _run(_go)


@tool
def hmc_rename_lpar(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    new_name: str,
    ownership_override: bool = False,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Rename one LPAR after enforcing its ownership token.

    Before renaming, inspect the description with hmc_get_lpar_description.
    Under the ADR 0011 advisory protocol, stop and ask the operator when its
    ownership token names a different agent.

    Args:
        system_name_or_uuid: SystemName or UUID containing the logical partition.
        lpar_name_or_uuid: Current PartitionName or UUID of the logical partition.
        new_name: Replacement PartitionName.
        ownership_override: Bypass ownership rejection only after explicit operator approval.
        profile: Optional configured HMC profile name; uses the default when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            try:
                _, updated = await rename_lpar(
                    hmc,
                    system_name_or_uuid,
                    lpar_name_or_uuid,
                    new_name,
                    ownership_override=ownership_override,
                )
            except HMCError as exc:
                _check_lpar_write_error(exc)
                raise
            return updated

    return _run(_go)


@tool
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

    Args:
        lpar_name_or_uuid: PartitionName or UUID of the running logical partition.
        resources: Processor fields to change; omitted fields stay unchanged.
        profile: Optional configured HMC profile name; uses the default when omitted.
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


@tool
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

    Args:
        lpar_name_or_uuid: PartitionName or UUID of the running logical partition.
        resources: Memory fields in MiB to change; omitted fields stay unchanged.
        profile: Optional configured HMC profile name; uses the default when omitted.
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


@tool(annotations=_DESTRUCTIVE)
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

    Args:
        system_name_or_uuid: SystemName or UUID containing the logical partition.
        lpar_name_or_uuid: PartitionName or UUID of the logical partition to delete.
        ownership_override: Bypass ownership rejection only after operator approval.
        profile: Optional configured HMC profile name; uses the default when omitted.

    Raises:
        HMCError: If the partition state is not 'not activated' (HTTP 409).
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            lpar_uuid = await delete_lpar(
                hmc,
                system_name_or_uuid,
                lpar_name_or_uuid,
                ownership_override=ownership_override,
            )
            return f"Deleted LPAR {lpar_uuid}"

    return _run(_go)


@tool
def hmc_power_on_lpar(
    lpar_name_or_uuid: str,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    force: bool = False,
    profile: str | None = None,
) -> LparPowerOnOutcome:
    """Submit a PowerOn job for a logical partition.

    lpar_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_list_lpars). Returns ``already_running``, nullable ``job``,
    and nullable ``message`` fields. A submitted job is in ``job``; check it
    with hmc_get_job. This changes the state of a real partition — confirm the
    target with hmc_get_lpar(lpar_name_or_uuid=...) before calling.

    If the partition is already in the 'running' state, ``already_running`` is
    true, ``job`` is null, and ``message`` explains that no job was submitted.
    Pass force=True to skip this check and submit PowerOn unconditionally.

    Set wait=True to block until the job reaches a terminal state or until
    timeout_seconds elapses; ``job`` then contains the last polled job.

    Args:
        lpar_name_or_uuid: PartitionName or UUID of the logical partition to power on.
        wait: Whether to poll the submitted job until terminal or timed out.
        timeout_seconds: Maximum polling duration in seconds when waiting.
        poll_interval: Seconds between job polls when waiting; must be positive.
        force: Submit PowerOn even when the partition already reports running.
        profile: Optional configured HMC profile name; uses the default when omitted.
    """

    validate_wait_timing(wait, timeout_seconds, poll_interval)

    async def _go():
        async with client_from_env(profile) as hmc:
            result = await power_lpar(
                hmc,
                lpar_name_or_uuid,
                power_on=True,
                force=force,
                wait=wait,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
            )
            return power_on_outcome(result)

    return _run(_go)


@tool(annotations=_DESTRUCTIVE)
def hmc_power_off_lpar(
    lpar_name_or_uuid: str,
    immediate: bool = False,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
) -> dict[str, Any] | None:
    """Submit a PowerOff job for a logical partition.

    lpar_name_or_uuid: accepts either a PartitionName or a UUID.
    system_name_or_uuid disambiguates duplicate partition names; it is ignored
    when lpar_name_or_uuid is already a UUID.
    immediate=True forces an immediate power off (no graceful OS shutdown).
    Returns the submitted job. This changes the state of a real partition.

    Set wait=True to block until the job reaches a terminal state.

    Args:
        lpar_name_or_uuid: PartitionName or UUID of the logical partition to power off.
        immediate: Whether to request immediate shutdown instead of graceful shutdown.
        wait: Whether to poll the submitted job until terminal or timed out.
        timeout_seconds: Maximum polling duration in seconds when waiting.
        poll_interval: Seconds between job polls when waiting; must be positive.
        profile: Optional configured HMC profile name; uses the default when omitted.
        system_name_or_uuid: Optional SystemName or UUID used to disambiguate its name.
    """

    validate_wait_timing(wait, timeout_seconds, poll_interval)

    async def _go():
        async with client_from_env(profile) as hmc:
            result = await power_lpar(
                hmc,
                lpar_name_or_uuid,
                power_on=False,
                system_name_or_uuid=system_name_or_uuid,
                immediate=immediate,
                wait=wait,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
            )
            return result.job

    return _run(_go)
