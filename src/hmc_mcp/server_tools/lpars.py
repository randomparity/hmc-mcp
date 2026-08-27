"""MCP tools for LPAR creation, mutation, deletion, and power control."""

from __future__ import annotations

from ..tool_registry import tool_module

from typing import Any

from .._app import (
    run_sync,
)
from ..errors import HMCError
from ..ssh import HMCCLIError
from ..client.client_factory import client_from_env
from ..resource_identity import resolve_lpar_uuid
from ..documents import (
    Keylock,
    LparResources,
    OsType,
    PartitionType,
    build_lpar_document,
)
from ..operations.decommission import DecommissionResult, decommission_lpar
from ..operations.lpar import (
    LparCreation,
    LparPowerOnOutcome,
    ProvisionAffinityAssessment,
    activation_allows_assessment,
    affinity_not_measured,
    assess_post_activation_affinity,
    classify_affinity_outcome,
    _check_lpar_write_error,
    create_and_stamp_lpar,
    clear_lpar_boot_order,
    delete_lpar,
    list_lpar_ownership,
    power_lpar,
    power_on_outcome,
    read_lpar_boot_order,
    rename_lpar,
    set_lpar_boot_order,
    set_lpar_memory,
    set_lpar_processors,
    validate_affinity_request,
)
from ..operations.assignments import (
    AssignmentStep,
    LparPcieAssignments,
    LparPcieWorkflowResult,
    _apply_validated_lpar_pcie_assignments,
    prevalidate_lpar_pcie_assignments,
)
from ..ssh_lpar import validate_caller_token

tool, register_tools, tool_security = tool_module()


# vNIC assignments name a nested VIOS that target extraction cannot authorize.
@tool(
    effect="mutate",
    operation="lpar.create",
    target_kind="managed_system",
    exhaustive_targets=False,
)
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
    caller_token: str | None = None,
    assignments: LparPcieAssignments = LparPcieAssignments(),
    profile: str | None = None,
) -> LparPcieWorkflowResult:
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
      With ``caller_token``, ``ownership_stamped=True`` confirms both the ownership
      stamp and the caller segment landed (one combined write); ``False`` means both
      were lost; ``None`` means the stamp was skipped — the reason is in ``warnings``.
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
        caller_token: Optional caller tracking reference embedded in the partition
            description as ``[caller <token>]`` after the ownership stamp (ADR 0064);
            1–64 printable ASCII characters, no whitespace or , = " [ ] \\.
        assignments: Declarative dedicated, direct SR-IOV, and vNIC requests.
        profile: Optional configured HMC profile name; uses the default when omitted.
    """

    if caller_token is not None:
        validate_caller_token(caller_token)

    async def _go():
        async with client_from_env(profile) as hmc:
            try:
                await prevalidate_lpar_pcie_assignments(
                    hmc, system_name_or_uuid, assignments
                )
                creation = await create_and_stamp_lpar(
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
                        caller_token,
                    ),
                )
                steps = [AssignmentStep("create", "ok", creation.lpar)]
                if creation.lpar is None:
                    return LparPcieWorkflowResult(
                        True,
                        False,
                        None,
                        creation.ownership_stamped,
                        tuple(steps),
                        creation.warnings,
                    )
                assignment_result = await _apply_validated_lpar_pcie_assignments(
                    hmc, system_name_or_uuid, name, assignments
                )
                steps.extend(assignment_result.steps)
                return LparPcieWorkflowResult(
                    True,
                    assignment_result.workflow_completed,
                    creation.lpar,
                    creation.ownership_stamped,
                    tuple(steps),
                    creation.warnings,
                )
            except HMCError as exc:
                _check_lpar_write_error(exc)
                raise

    return run_sync(_go)


# Assignment collections can name both a managed system and a nested VIOS.
@tool(
    effect="mutate",
    operation="lpar.modify",
    target_kind="lpar",
    exhaustive_targets=False,
)
def hmc_modify_lpar(
    lpar_name_or_uuid: str,
    resources: LparResources = LparResources(),
    system_name_or_uuid: str | None = None,
    assignments: LparPcieAssignments = LparPcieAssignments(),
    ownership_override: bool = False,
    profile: str | None = None,
) -> LparPcieWorkflowResult:
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
        system_name_or_uuid: Managed-system selector required when assignments are present.
        assignments: Declarative dedicated, direct SR-IOV, and vNIC requests.
        ownership_override: Bypass assignment ownership rejection after operator approval.
        profile: Optional configured HMC profile name; uses the default when omitted.
    """
    xml = build_lpar_document(name=None, resources=resources)

    async def _go():
        async with client_from_env(profile) as hmc:
            if assignments != LparPcieAssignments() and system_name_or_uuid is None:
                raise ValueError("system_name_or_uuid is required for PCIe assignments")
            if system_name_or_uuid is not None:
                await prevalidate_lpar_pcie_assignments(
                    hmc, system_name_or_uuid, assignments
                )
            modified = None
            steps: list[AssignmentStep] = []
            if resources != LparResources():
                lpar_uuid = await resolve_lpar_uuid(hmc, lpar_name_or_uuid)
                try:
                    modified = await hmc.modify_logical_partition(lpar_uuid, xml)
                except HMCError as exc:
                    _check_lpar_write_error(exc)
                    raise
                steps.append(AssignmentStep("resources", "ok", modified))
            assignment_result = await _apply_validated_lpar_pcie_assignments(
                hmc,
                system_name_or_uuid or "",
                lpar_name_or_uuid,
                assignments,
                ownership_override=ownership_override,
            )
            steps.extend(assignment_result.steps)
            return LparPcieWorkflowResult(
                False,
                assignment_result.workflow_completed,
                modified,
                None,
                tuple(steps),
                (),
            )

    return run_sync(_go)


@tool(effect="mutate", operation="lpar.rename", target_kind="lpar")
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

    return run_sync(_go)


@tool(effect="mutate", operation="lpar.dlpar_proc", target_kind="lpar")
def hmc_dlpar_proc(
    lpar_name_or_uuid: str,
    resources: LparResources = LparResources(),
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
    ownership_override: bool = False,
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
        system_name_or_uuid: Optional SystemName or UUID that disambiguates the
            partition name; when omitted the name is searched fleet-wide and the
            owning system is discovered for the ownership check.
        ownership_override: Bypass ownership rejection only after explicit
            operator approval.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await set_lpar_processors(
                hmc,
                system_name_or_uuid,
                lpar_name_or_uuid,
                resources,
                ownership_override=ownership_override,
            )

    return run_sync(_go)


@tool(effect="mutate", operation="lpar.dlpar_mem", target_kind="lpar")
def hmc_dlpar_mem(
    lpar_name_or_uuid: str,
    resources: LparResources = LparResources(),
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
    ownership_override: bool = False,
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
        system_name_or_uuid: Optional SystemName or UUID that disambiguates the
            partition name; when omitted the name is searched fleet-wide and the
            owning system is discovered for the ownership check.
        ownership_override: Bypass ownership rejection only after explicit
            operator approval.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await set_lpar_memory(
                hmc,
                system_name_or_uuid,
                lpar_name_or_uuid,
                resources,
                ownership_override=ownership_override,
            )

    return run_sync(_go)


@tool(effect="destructive", operation="lpar.delete", target_kind="lpar")
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

    return run_sync(_go)


@tool(effect="destructive", operation="lpar.decommission", target_kind="lpar")
def hmc_decommission_lpar(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    dry_run: bool = False,
    ownership_override: bool = False,
    immediate: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
) -> DecommissionResult:
    """Inventory, authorize, and optionally decommission one LPAR.

    This tool orchestrates the high-risk decommission workflow in one call:
    resolve the target LPAR on the selected managed system, enforce the
    ownership token, inventory its adapter and observed storage blast radius,
    power it off when needed, detach client adapters, and finally delete the
    partition. Set dry_run=True to render the blast radius and step plan
    without mutating anything. With dry_run=False, the final delete is
    irreversible once reached.

    Ownership enforcement runs even for dry runs. If the LPAR description
    names a different owner, stop and ask the operator before proceeding. Set
    ownership_override=True only after explicit operator approval.

    Returns a structured result with these fields:

    - ``resource_deleted`` — whether the final LPAR delete completed.
    - ``workflow_completed`` — whether every requested workflow step completed.
    - ``lpar_uuid`` — UUID of the resolved target LPAR.
    - ``dry_run`` — whether the call only inventoried the blast radius.
    - ``steps`` — ordered per-step status and curated result records.
    - ``warnings`` — non-fatal warnings discovered during inventory.
    - ``blast_radius`` — curated inventory of the LPAR, adapters, and observed
      storage mappings.

    Args:
        system_name_or_uuid: SystemName or UUID of the managed system containing the target LPAR.
        lpar_name_or_uuid: PartitionName or UUID of the logical partition to inventory or delete.
        dry_run: When True, inventory the blast radius and planned steps without mutating resources.
        ownership_override: Bypass ownership rejection only after explicit operator approval.
        immediate: Whether to request immediate shutdown instead of graceful shutdown
            before deletion.
        timeout_seconds: Maximum polling duration in seconds for the power-off job;
            must be positive.
        poll_interval: Seconds between power-off job polls; must be positive.
        profile: Optional configured HMC profile name; uses the default when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await decommission_lpar(
                hmc,
                system_name_or_uuid,
                lpar_name_or_uuid,
                dry_run=dry_run,
                ownership_override=ownership_override,
                immediate=immediate,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
            )

    return run_sync(_go)


@tool(effect="mutate", operation="lpar.power_on", target_kind="lpar")
def hmc_power_on_lpar(
    lpar_name_or_uuid: str,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    force: bool = False,
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
    affinity_assessment: ProvisionAffinityAssessment | None = None,
    ownership_override: bool = False,
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
        system_name_or_uuid: Optional SystemName or UUID that disambiguates the
            partition name; when omitted the name is searched fleet-wide. With
            HMC_AUTHORIZE_POWER_OPERATIONS set it also spares the ownership
            guard a fleet-wide search for the partition's owning system.
        affinity_assessment: Optional target-bound captured affinity evidence and
            explicit warning or fail-closed response intent.
        ownership_override: Bypass ADR 0011 ownership rejection only after operator
            approval; has no effect unless HMC_AUTHORIZE_POWER_OPERATIONS is set.
    """

    if affinity_assessment is not None:
        if system_name_or_uuid is None:
            raise ValueError(
                "system_name_or_uuid is required for post-activation affinity assessment"
            )
        if affinity_assessment.system_name_or_uuid != system_name_or_uuid:
            raise ValueError(
                "affinity assessment managed-system identity must match target"
            )
        if affinity_assessment.lpar_name != lpar_name_or_uuid:
            raise ValueError("affinity assessment LPAR identity must match target")
        validate_affinity_request(affinity_assessment)

    async def _go():
        async with client_from_env(profile) as hmc:
            result = await power_lpar(
                hmc,
                system_name_or_uuid,
                lpar_name_or_uuid,
                power_on=True,
                force=force,
                wait=wait,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
                ownership_override=ownership_override,
            )
            if (
                affinity_assessment is None
                or result.job is None
                or result.job.get("already_running") is True
            ):
                return power_on_outcome(result)
            if not wait:
                return power_on_outcome(
                    result,
                    affinity_not_measured(
                        "skipped",
                        "Assessment requires wait=true to observe successful activation.",
                    ),
                )
            successful, reason = activation_allows_assessment(result)
            if not successful:
                status = (
                    "failed"
                    if affinity_assessment.response == "fail"
                    else "unavailable"
                )
                return power_on_outcome(result, affinity_not_measured(status, reason))
            try:
                assessment = await assess_post_activation_affinity(
                    hmc, affinity_assessment
                )
            except (HMCError, HMCCLIError, ValueError) as exc:
                status = (
                    "failed"
                    if affinity_assessment.response == "fail"
                    else "unavailable"
                )
                return power_on_outcome(
                    result,
                    affinity_not_measured(
                        status, f"Affinity measurement unavailable: {exc}"
                    ),
                )
            return power_on_outcome(
                result,
                classify_affinity_outcome(assessment, affinity_assessment.response),
            )

    return run_sync(_go)


@tool(effect="destructive", operation="lpar.power_off", target_kind="lpar")
def hmc_power_off_lpar(
    lpar_name_or_uuid: str,
    immediate: bool = False,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
    ownership_override: bool = False,
) -> dict[str, Any] | None:
    """Submit a PowerOff job for a logical partition.

    lpar_name_or_uuid: accepts either a PartitionName or a UUID.
    system_name_or_uuid disambiguates duplicate partition names; it is otherwise
    unused when lpar_name_or_uuid is already a UUID, unless the server runs with
    HMC_AUTHORIZE_POWER_OPERATIONS set, where it also spares the ownership guard
    a fleet-wide search for the partition's owning system.
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
            With HMC_AUTHORIZE_POWER_OPERATIONS set it also spares the ownership
            guard a fleet-wide search for the partition's owning system.
        ownership_override: Bypass ADR 0011 ownership rejection only after operator
            approval; has no effect unless HMC_AUTHORIZE_POWER_OPERATIONS is set.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            result = await power_lpar(
                hmc,
                system_name_or_uuid,
                lpar_name_or_uuid,
                power_on=False,
                immediate=immediate,
                wait=wait,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
                ownership_override=ownership_override,
            )
            return result.job

    return run_sync(_go)


# ====================================================================== #
# LPAR Boot Order Tools
# ====================================================================== #


@tool(effect="read", operation="boot_order.read", target_kind="lpar")
def hmc_read_lpar_boot_order(
    system_name_or_uuid: str,
    lpar_uuid: str,
    profile: str | None = None,
) -> dict[str, Any]:
    """Read an LPAR's boot order state (pending and current).

    Args:
        system_name_or_uuid: CLI name or UUID of the managed system.
        lpar_uuid: UUID of the logical partition.
        profile: Optional configured HMC profile name; uses the default when omitted.

    Returns the boot device order for the LPAR, including both the pending
    boot string (next boot) and the current boot device list.

    Args:
        system_name_or_uuid: CLI name or UUID of the managed system.
        lpar_uuid: UUID of the logical partition.

    Returns:
        Dictionary with boot order information containing:
        - lpar_uuid: UUID of the LPAR
        - lpar_name: Name of the LPAR
        - pending_boot_string: The PendingBootString for the next boot
        - boot_device_list: The current BootDeviceList
        - last_booted_device_string: The device used on last boot
    """

    async def _go() -> dict[str, Any]:
        async with client_from_env(profile) as hmc:
            result = await read_lpar_boot_order(
                hmc,
                system_name_or_uuid=system_name_or_uuid,
                lpar_uuid=lpar_uuid,
            )
            return result

    return run_sync(_go)


@tool(effect="mutate", operation="boot_order.set", target_kind="lpar")
def hmc_set_lpar_boot_order(
    system_name_or_uuid: str,
    lpar_uuid: str,
    devices: list[str],
    *,
    ownership_override: bool = False,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Set an LPAR's boot order to a validated device selector list.

    Sets the PendingBootString to an ordered list of boot device selectors.
    Changes take effect on the next LPAR activation (no reboot required).

    Args:
        system_name_or_uuid: CLI name or UUID of the managed system.
        lpar_uuid: UUID of the logical partition.
        devices: Ordered list of boot device selectors (cd, disk, network).
                 The first device is tried first, then the second, etc.
        ownership_override: If True, skip ownership token validation.
        profile: Optional configured HMC profile name; uses the default when omitted.

    Args:
        system_name_or_uuid: CLI name or UUID of the managed system.
        lpar_uuid: UUID of the logical partition.
        devices: Ordered list of boot device selectors (cd, disk, network).
                 The first device is tried first, then the second, etc.
        ownership_override: If True, skip ownership token validation.

    Returns:
        Updated LPAR resource if successful, None otherwise.

    Example:
        Set boot order to try network first, then CD, then disk:

        >>> hmc_set_lpar_boot_order(
        ...     "system1",
        ...     "lpar-uuid-123",
        ...     ["network", "cd", "disk"]
        ... )
    """

    async def _go() -> dict[str, Any] | None:
        async with client_from_env(profile) as hmc:
            result = await set_lpar_boot_order(
                hmc,
                system_name_or_uuid=system_name_or_uuid,
                lpar_uuid=lpar_uuid,
                devices=devices,
                ownership_override=ownership_override,
            )
            return result

    return run_sync(_go)


@tool(effect="mutate", operation="boot_order.clear", target_kind="lpar")
def hmc_clear_lpar_boot_order(
    system_name_or_uuid: str,
    lpar_uuid: str,
    *,
    ownership_override: bool = False,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Clear an LPAR's boot order (restore HMC defaults).

    Clears the PendingBootString, restoring the default boot behavior.
    Changes take effect on the next LPAR activation (no reboot required).

    Args:
        system_name_or_uuid: CLI name or UUID of the managed system.
        lpar_uuid: UUID of the logical partition.
        ownership_override: If True, skip ownership token validation.
        profile: Optional configured HMC profile name; uses the default when omitted.

    Args:
        system_name_or_uuid: CLI name or UUID of the managed system.
        lpar_uuid: UUID of the logical partition.
        ownership_override: If True, skip ownership token validation.

    Returns:
        Updated LPAR resource if successful, None otherwise.
    """

    async def _go() -> dict[str, Any] | None:
        async with client_from_env(profile) as hmc:
            result = await clear_lpar_boot_order(
                hmc,
                system_name_or_uuid=system_name_or_uuid,
                lpar_uuid=lpar_uuid,
                ownership_override=ownership_override,
            )
            return result

    return run_sync(_go)


@tool(effect="read", operation="lpar.list_ownership", target_kind="managed_system")
def hmc_list_lpar_ownership(
    system_name_or_uuid: str | None = None,
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """Read parsed ownership for every LPAR on a system in one REST call.

    Parses the advisory ADR 0011 ownership token out of each partition's
    description via the bulk list feed, so one request covers the whole system
    (#375). Every partition is returned: ``owned`` partitions carry the
    ``owner`` agent id; a description with no well-formed stamp is reported
    with ``unparsed=True``; a partition with no description at all has
    ``description=None`` — the three facts stay distinct for reconciliation.

    Args:
        system_name_or_uuid: Optional SystemName or UUID whose partitions to
            read; omitted reads the fleet-wide LogicalPartition feed in one
            call (entries then carry no parent-system attribution).
        profile: Optional configured HMC profile name; uses the default when
            omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await list_lpar_ownership(hmc, system_name_or_uuid)

    return run_sync(_go)
