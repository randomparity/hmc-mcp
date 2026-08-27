"""Shared LPAR creation and ownership operations."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from hmc_mcp.affinity_assessment import (
    LparAffinityAssessmentOutcome,
    affinity_not_measured,
)

from ..client import HMCClient
from ..client.client_resolution import (
    MAX_PARENT_DISCOVERY_SYSTEMS,
    PARENT_DISCOVERY_TIMEOUT_SECONDS,
)
from ..resource_identity import is_uuid, resolve_lpar_uuid, resolve_system_uuid
from ..documents import (
    BOOT_DEVICE_SELECTORS,
    Keylock,
    LparResources,
    OsType,
    PartitionType,
    build_boot_order_document,
    build_clear_boot_order_document,
    build_dlpar_mem_document,
    build_dlpar_proc_document,
    build_lpar_document,
)
from ..errors import HMCError
from ..jobs import (
    DEFAULT_JOB_POLL_INTERVAL,
    DEFAULT_JOB_TIMEOUT_SECONDS,
    SUCCESSFUL_JOB_STATUSES,
    job_outcome,
    power_off_lpar_job,
    power_on_lpar_job,
    validate_wait_timing,
    wait_for_submitted_job,
)
from ..lpar_ownership import (
    authorize_lpar_mutation,
    resolve_lpar_ownership_names,
    stamp_created_lpar_ownership,
)
from ..ssh.transport import HMCCLIError
from ..ssh.lpar import (
    _ssh_system_name,
    create_lpar_via_cli,
    validate_caller_token,
)
from .assignments import (
    AssignmentStep,
    LparPcieAssignments,
    LparPcieWorkflowResult,
    _apply_validated_lpar_pcie_assignments,
    prevalidate_lpar_pcie_assignments,
)

_logger = logging.getLogger(__name__)

PartitionState = Literal[
    "running",
    "not activated",
    "starting",
    "shutting down",
    "stopping",
    "open firmware",
    "error",
    "migrating",
    "suspended",
    "resuming",
    "unknown",
]
PARTITION_STATES: frozenset[PartitionState] = frozenset(
    {
        "running",
        "not activated",
        "starting",
        "shutting down",
        "stopping",
        "open firmware",
        "error",
        "migrating",
        "suspended",
        "resuming",
        "unknown",
    }
)
ProcessorCompatibilityMode = Literal[
    "default",
    "POWER5",
    "POWER6",
    "POWER6+",
    "POWER7",
    "POWER8",
    "POWER9_Base",
    "POWER9",
    "POWER10",
    "POWER11",
]
PROCESSOR_COMPATIBILITY_MODES: frozenset[ProcessorCompatibilityMode] = frozenset(
    {
        "default",
        "POWER5",
        "POWER6",
        "POWER6+",
        "POWER7",
        "POWER8",
        "POWER9_Base",
        "POWER9",
        "POWER10",
        "POWER11",
    }
)


def translate_lpar_write_error(exc: HMCError) -> None:
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


async def _modify_lpar(
    hmc: HMCClient,
    lpar_name_or_uuid: str,
    resources: LparResources,
    system_name_or_uuid: str | None,
    assignments: LparPcieAssignments,
    *,
    ownership_override: bool = False,
) -> LparPcieWorkflowResult:
    """Apply resource and PCIe changes as one ordered LPAR workflow."""
    if assignments != LparPcieAssignments() and system_name_or_uuid is None:
        raise ValueError("system_name_or_uuid is required for PCIe assignments")
    if system_name_or_uuid is not None:
        await prevalidate_lpar_pcie_assignments(hmc, system_name_or_uuid, assignments)

    modified = None
    steps: list[AssignmentStep] = []
    if resources != LparResources():
        lpar_uuid = await resolve_lpar_uuid(hmc, lpar_name_or_uuid)
        try:
            modified = await hmc.modify_logical_partition(
                lpar_uuid, build_lpar_document(name=None, resources=resources)
            )
        except HMCError as exc:
            translate_lpar_write_error(exc)
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


@dataclass(frozen=True)
class LparCreation:
    """Inputs needed by both REST and CLI LPAR creation paths."""

    name: str
    partition_type: PartitionType
    resources: LparResources
    partition_id: int | None = None
    os_type: OsType | None = None
    keylock: Keylock | None = None
    max_virtual_slots: int | None = None
    caller_token: str | None = None
    stamp_policy: Literal["best-effort", "required"] = "best-effort"


@dataclass(frozen=True)
class LparCreationResult:
    """Result shared by direct creation and provisioning workflows."""

    resource_created: bool
    lpar: dict[str, Any] | None
    ownership_stamped: bool | None
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class LparPowerResult:
    """An LPAR power outcome paired with its resolved identity."""

    lpar_uuid: str
    job: dict[str, Any] | None


@dataclass(frozen=True)
class LparPowerOnOutcome:
    """Stable public result for an LPAR PowerOn request."""

    already_running: bool
    job: dict[str, Any] | None
    message: str | None
    affinity_assessment: LparAffinityAssessmentOutcome


def power_on_outcome(
    result: LparPowerResult,
    affinity_assessment: LparAffinityAssessmentOutcome | None = None,
) -> LparPowerOnOutcome:
    """Normalize submitted and already-running PowerOn results."""
    job = result.job
    if job is not None and job.get("already_running") is True:
        message = job.get("message")
        return LparPowerOnOutcome(
            already_running=True,
            job=None,
            message=message if isinstance(message, str) else None,
            affinity_assessment=affinity_assessment
            or affinity_not_measured(
                "skipped",
                "No activation was observed because the LPAR was already running.",
            ),
        )
    return LparPowerOnOutcome(
        already_running=False,
        job=job,
        message=None,
        affinity_assessment=affinity_assessment
        or affinity_not_measured(
            "skipped", "Post-activation assessment was not requested."
        ),
    )


def activation_allows_assessment(result: LparPowerResult) -> tuple[bool, str]:
    """Return whether a waited PowerOn result proves successful activation."""
    outcome = job_outcome("PowerOn", result.job)
    if outcome.timed_out:
        return False, "PowerOn did not reach a terminal status before timeout."
    if outcome.status not in SUCCESSFUL_JOB_STATUSES:
        return False, outcome.error or f"PowerOn ended with status {outcome.status}."
    return True, "PowerOn reached a successful terminal status."


async def create_and_stamp_lpar(
    hmc: HMCClient,
    system_name_or_uuid: str,
    creation: LparCreation,
) -> LparCreationResult:
    """Validate and create an LPAR with fallback and ownership stamping.

    ``creation.stamp_policy`` selects how an ownership-stamp failure or skip
    is surfaced (issue #377):

    - ``"best-effort"`` (default, ADR 0011): a failed or skipped stamp never
      fails the call. The result reports it through ``ownership_stamped``
      and ``warnings``.
    - ``"required"``: any outcome other than a confirmed stamp raises
      :class:`HMCError` *after* the create completes. The exception carries
      the new LPAR's name and UUID so the caller can find the partition.
      The LPAR **still exists** when the error is raised — the create is not
      rolled back — so re-stamp it with
      :func:`set_lpar_ownership_description` (issue #376, ADR 0066) or
      delete it to release its resources.
    """
    if creation.stamp_policy not in ("best-effort", "required"):
        raise ValueError(
            f"stamp_policy must be 'best-effort' or 'required', "
            f"got {creation.stamp_policy!r}"
        )
    if creation.caller_token is not None:
        # First statement, before find_partition_by_name and outside the
        # stamp's best-effort catch: no create can precede rejection, and a
        # malformed token can never discard the ownership stamp (ADR 0064).
        validate_caller_token(creation.caller_token)
    existing = await hmc.find_partition_by_name(creation.name)
    if existing:
        raise ValueError(
            f"An LPAR named {creation.name!r} already exists "
            f"(UUID {existing.get('UUID')!r}). Choose a different name "
            "or delete the existing partition first."
        )
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    system_name: str | None = None
    document = build_lpar_document(
        name=creation.name,
        partition_type=creation.partition_type,
        partition_id=creation.partition_id,
        resources=creation.resources,
        os_type=creation.os_type,
        keylock=creation.keylock,
        max_virtual_slots=creation.max_virtual_slots,
    )
    try:
        created_lpar = await hmc.create_logical_partition(system_uuid, document)
    except HMCError as exc:
        if exc.status_code != 406:
            raise
        try:
            system_name = await _ssh_system_name(hmc.config, system_uuid)
        except HMCCLIError:
            system_name = system_name_or_uuid
        resources = creation.resources
        await create_lpar_via_cli(
            hmc.config,
            system_name=system_name,
            name=creation.name,
            partition_type=creation.partition_type,
            resources=resources,
            max_virtual_slots=creation.max_virtual_slots,
        )
        created_lpar = await hmc.find_partition_by_name(creation.name)

    if created_lpar is None:
        if creation.stamp_policy == "required":
            raise HMCError(
                "stamp_policy='required': cannot confirm the created LPAR "
                f"exists — the create returned no body for {creation.name!r}. "
                "Verify whether the partition was created before retrying; a "
                "created partition can be re-stamped with "
                "set_lpar_ownership_description."
            )
        return LparCreationResult(
            resource_created=True,
            lpar=None,
            ownership_stamped=None,
            warnings=(
                f"ownership stamp skipped for LPAR {creation.name!r}: "
                "create returned no LPAR body",
            ),
        )
    ownership_stamped, warnings = await stamp_created_lpar_ownership(
        hmc,
        system_uuid,
        system_name or system_name_or_uuid,
        created_lpar,
        caller_token=creation.caller_token,
    )
    if creation.stamp_policy == "required" and ownership_stamped is not True:
        resource = created_lpar.get("Resource") or {}
        name = resource.get("PartitionName") or creation.name
        uuid = created_lpar.get("UUID")
        identity = f"{name!r} (UUID {uuid!r})" if uuid else f"{name!r} (UUID unknown)"
        raise HMCError(
            "stamp_policy='required': ownership stamping did not succeed for "
            f"LPAR {identity}. {'; '.join(warnings)} The LPAR still exists — "
            "the create is not rolled back. Re-stamp it with "
            "set_lpar_ownership_description (issue #376) or delete it to "
            "release its resources."
        )
    return LparCreationResult(True, created_lpar, ownership_stamped, tuple(warnings))


async def delete_lpar(
    hmc: HMCClient,
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    *,
    ownership_override: bool = False,
) -> str:
    """Authorize and delete a powered-off LPAR, returning its UUID."""
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    lpar_uuid = await resolve_lpar_uuid(
        hmc, lpar_name_or_uuid, system_name_or_uuid=system_uuid
    )
    system_name, lpar_name = await resolve_lpar_ownership_names(
        hmc, system_uuid, system_name_or_uuid, lpar_uuid
    )
    await authorize_lpar_mutation(
        hmc,
        system_name,
        lpar_name,
        ownership_override=ownership_override,
    )
    state = await hmc.get_quick_property(
        "LogicalPartition", lpar_uuid, "PartitionState"
    )
    if state != "not activated":
        raise HMCError(
            f"Cannot delete LPAR {lpar_uuid} — current state is {state!r}; "
            "it must be 'not activated' to delete. Power off the partition "
            "and verify its state before retrying.",
            status_code=409,
        )
    await hmc.delete_logical_partition(lpar_uuid)
    return lpar_uuid


async def power_lpar(
    hmc: HMCClient,
    system_name_or_uuid: str | None,
    lpar_name_or_uuid: str,
    *,
    power_on: bool,
    immediate: bool = False,
    force: bool = False,
    wait: bool = False,
    timeout_seconds: int = DEFAULT_JOB_TIMEOUT_SECONDS,
    poll_interval: int = DEFAULT_JOB_POLL_INTERVAL,
    ownership_override: bool = False,
) -> LparPowerResult:
    """Apply shared LPAR power policy, submit the job, and optionally wait.

    ADR 0011 ownership is advisory here by default. Powering a partition another
    agent owns is only rejected when the operator sets
    ``authorize_power_operations`` (``HMC_AUTHORIZE_POWER_OPERATIONS``), the
    opt-in ADR 0092 §4 records; with the setting off this call reads no
    ownership token and opens no SSH connection, so a caller that cares should
    read the description itself — ``list_lpar_ownership`` reports it for a whole
    managed system in one REST call.

    With the setting on the resolve chain is ADR 0094's
    :func:`_resolve_and_authorize_lpar`, shared with the DLPAR operations —
    the same shape, because these are the operations whose managed-system
    selector is optional (ADR 0063). It derives the owning system when the
    caller omits the selector, and confirms the partition lives on the system
    the caller named when they supply one, so the token read is never taken
    from a system the partition does not belong to.

    ``ownership_override=True`` bypasses the rejection for this one call and
    records an audited override. It skips the SSH ownership read and the fleet
    walk, not the partition-name read that names the audit record. When the
    ownership read fails or times out, the call fails with
    :class:`HMCCLIError` and submits no job.
    """
    validate_wait_timing(wait, timeout_seconds, poll_interval)
    if hmc.config.authorize_power_operations:
        lpar_uuid = await _resolve_and_authorize_lpar(
            hmc,
            lpar_name_or_uuid,
            system_name_or_uuid,
            ownership_override=ownership_override,
        )
    else:
        lpar_uuid = await resolve_lpar_uuid(
            hmc, lpar_name_or_uuid, system_name_or_uuid=system_name_or_uuid
        )
    if power_on and not force:
        state = await hmc.get_quick_property(
            "LogicalPartition", lpar_uuid, "PartitionState"
        )
        if state == "running":
            return LparPowerResult(
                lpar_uuid,
                {
                    "already_running": True,
                    "message": (
                        f"LPAR {lpar_uuid} is already running. "
                        "Use force=True to submit PowerOn anyway."
                    ),
                },
            )
    operation = "PowerOn" if power_on else "PowerOff"
    document = (
        power_on_lpar_job() if power_on else power_off_lpar_job(immediate=immediate)
    )
    job = await hmc.submit_job(
        f"/rest/api/uom/LogicalPartition/{lpar_uuid}/do/{operation}", document
    )
    selected_job = await wait_for_submitted_job(
        hmc, job, wait, timeout_seconds, poll_interval
    )
    return LparPowerResult(lpar_uuid, selected_job)


async def rename_lpar(
    hmc: HMCClient,
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    new_name: str,
    *,
    ownership_override: bool = False,
) -> tuple[str, dict[str, Any] | None]:
    """Resolve, authorize, and rename one LPAR."""
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    lpar_uuid = await resolve_lpar_uuid(
        hmc, lpar_name_or_uuid, system_name_or_uuid=system_uuid
    )
    system_name, lpar_name = await resolve_lpar_ownership_names(
        hmc, system_uuid, system_name_or_uuid, lpar_uuid
    )
    await authorize_lpar_mutation(
        hmc,
        system_name,
        lpar_name,
        ownership_override=ownership_override,
    )
    updated = await hmc.modify_logical_partition(
        lpar_uuid, build_lpar_document(name=new_name)
    )
    return lpar_uuid, updated


# ====================================================================== #
# DLPAR Resource Operations
# ====================================================================== #


def _fleet_within_discovery_bound(
    systems: list[dict[str, Any]], lpar_label: str
) -> list[dict[str, Any]]:
    """Reject an owning-system search whose request fan-out is too large.

    The bound is `client_resolution`'s, but not its message: that one reads
    "ambiguous LPAR name", and nothing is ambiguous here — the partition UUID
    resolved uniquely before this ran.
    """
    if len(systems) > MAX_PARENT_DISCOVERY_SYSTEMS:
        raise ValueError(
            f"Cannot identify the managed system owning LPAR {lpar_label!r}: "
            f"discovery exceeds {MAX_PARENT_DISCOVERY_SYSTEMS} managed systems; "
            "supply managed-system scope"
        )
    return systems


def _discovery_candidate(system: dict[str, Any]) -> tuple[str, str] | None:
    """Return one fleet entry's UUID and CLI name, or None if unusable."""
    system_uuid = system.get("UUID")
    system_name = (system.get("Resource") or {}).get("SystemName")
    if not isinstance(system_uuid, str) or not system_uuid:
        return None
    if not isinstance(system_name, str) or not system_name:
        return None
    return system_uuid, system_name


def _hosts_partition(partitions: list[dict[str, Any]], lpar_uuid: str) -> bool:
    """Whether *partitions* contains *lpar_uuid*, compared case-insensitively.

    ``resolve_lpar_uuid`` returns a caller-supplied UUID verbatim and ``is_uuid``
    admits upper-case hex, while the HMC renders UUIDs lower-case. These are the
    only places in the package where a *user-supplied* UUID is equality-compared
    against HMC output, so the normalisation belongs here rather than in the
    shared resolver, whose value also reaches URL path segments.
    """
    wanted = lpar_uuid.casefold()
    return any(
        str(entry.get("UUID") or "").casefold() == wanted for entry in partitions
    )


@dataclass
class _SkippedFrames:
    """Frames the owning-system walk could not read, bounded for reporting."""

    unreadable: list[str] = field(default_factory=list)
    unusable_entries: int = 0

    @property
    def total(self) -> int:
        return len(self.unreadable) + self.unusable_entries

    def describe(self) -> str:
        """Render the skips as bounded evidence, never one line per frame."""
        if not self.total:
            return ""
        shown = self.unreadable[:_MAX_REPORTED_SKIPS]
        parts = list(shown)
        if len(self.unreadable) > len(shown):
            parts.append(f"and {len(self.unreadable) - len(shown)} more")
        if self.unusable_entries:
            parts.append(f"{self.unusable_entries} with incomplete inventory metadata")
        return f" ({self.total} could not be read: {', '.join(parts)})"


_MAX_REPORTED_SKIPS = 5


async def _fleet_inventory(hmc: HMCClient, lpar_label: str) -> list[dict[str, Any]]:
    """Read the fleet, translating an inventory failure into the same remedy.

    The unfiltered ``ManagedSystem`` feed is the one some HMC firmware builds
    answer with HTTP 500 (``client_systems.list_managed_systems``), and a
    selector-less DLPAR call reads it where it previously read nothing. Every
    other discovery failure names ``supply managed-system scope``; without this
    the one degraded-dependency case that the selector *does* fix would be the
    only one that never mentions it.
    """
    try:
        return await hmc.list_managed_systems()
    except HMCError as exc:
        raise ValueError(
            f"Cannot identify the managed system owning LPAR {lpar_label!r}: "
            f"managed-system inventory is unavailable ({exc}); "
            "supply managed-system scope"
        ) from exc


async def _search_fleet_for_partition(
    hmc: HMCClient, lpar_uuid: str, lpar_label: str, skipped: _SkippedFrames
) -> tuple[str, str] | None:
    """Return the system containing *lpar_uuid*, recording frames it could not read."""
    for system in _fleet_within_discovery_bound(
        await _fleet_inventory(hmc, lpar_label), lpar_label
    ):
        candidate = _discovery_candidate(system)
        if candidate is None:
            skipped.unusable_entries += 1
            continue
        system_uuid, system_name = candidate
        try:
            partitions = await hmc.list_logical_partitions(system_uuid)
        except HMCError as exc:
            _logger.warning(
                "Owning-system discovery could not read managed system %s: %s",
                system_uuid,
                exc,
                exc_info=exc,
            )
            skipped.unreadable.append(system_uuid)
            continue
        if _hosts_partition(partitions, lpar_uuid):
            _logger.info(
                "LPAR %s authorized against discovered managed system %s (%s)",
                lpar_uuid,
                system_name,
                system_uuid,
            )
            return system_uuid, system_name
    return None


async def _discover_owning_system(
    hmc: HMCClient, lpar_uuid: str, lpar_label: str
) -> tuple[str, str]:
    """Return the UUID and CLI name of the system that contains *lpar_uuid*.

    ADR 0094. The ADR 0011 guard reads an ownership token by CLI system name
    plus partition name, so an operation whose managed-system selector is
    optional (ADR 0063) has to derive one when the caller omits it. This is the
    bounded parent discovery :meth:`HMCClient.find_partition_by_name` already
    applies to a fleet-ambiguous partition name — the same 100-system fan-out
    cap, the same timeout, and the same "supply managed-system scope" remedy.

    The name comes from the inventory entry that matched, so *on this branch*
    the guard cannot fall back to running ``lssyscfg -m <uuid>``, which the HMC
    CLI cannot satisfy. The selector-supplied branch still hands ``_system_name``
    the caller's raw selector, which that helper's pre-existing degraded path
    can return verbatim.

    A frame whose partition feed errors, or whose inventory entry is unusable,
    is skipped rather than made fatal: the walk crosses frames the caller never
    named, and one unhealthy frame sorting early must not take DLPAR down for
    every partition in the fleet. Skipping never widens what may be mutated —
    the search still ends in a raise unless it *positively* matched the
    partition — and the raise names how many frames went unread, so a degraded
    fleet is diagnosed rather than reported as an absent partition.
    """
    skipped = _SkippedFrames()
    try:
        async with asyncio.timeout(PARENT_DISCOVERY_TIMEOUT_SECONDS):
            found = await _search_fleet_for_partition(
                hmc, lpar_uuid, lpar_label, skipped
            )
    except TimeoutError as exc:
        raise ValueError(
            f"Cannot identify the managed system owning LPAR {lpar_label!r}: "
            "parent discovery timed out; supply managed-system scope"
        ) from exc
    if found is not None:
        return found
    raise ValueError(
        f"Cannot identify the managed system owning LPAR {lpar_label!r}: "
        f"no managed system reports it{skipped.describe()}; "
        "supply managed-system scope"
    )


async def _verify_partition_on_system(
    hmc: HMCClient, system_uuid: str, lpar_uuid: str, lpar_selector: str
) -> None:
    """Reject a partition UUID that does not live on the selected system.

    ADR 0094. ``resolve_lpar_uuid`` passes a canonical UUID straight through, so
    a caller can pair any partition UUID with any managed-system selector —
    and ADR 0039 actively recommends UUIDs in policy allowlists, so that pairing
    is the recommended input shape. Without this check the guard would read
    ``lssyscfg -m <the selected system> --filter lpar_names=<the partition's
    name>``: on a cross-system name collision that reads a *different*
    partition's token, and can approve mutating a foreign-owned one. The
    omitted-selector path gets the same containment from
    :func:`_discover_owning_system`'s UUID match.

    Only a UUID needs the read, so *lpar_selector* must be the string the caller
    passed, not a resolved value: a partition *name* was resolved through
    ``find_partition_by_name`` against this very feed, which establishes its
    containment already, and re-reading the largest payload in the guarded chain
    would answer a settled question.
    """
    if not is_uuid(lpar_selector):
        return
    try:
        partitions = await hmc.list_logical_partitions(system_uuid)
    except HMCError as exc:
        raise ValueError(
            f"Cannot confirm LPAR {lpar_selector!r} belongs to managed system "
            f"{system_uuid} ({exc}); retry, or omit the selector to have the "
            "system discovered"
        ) from exc
    if not _hosts_partition(partitions, lpar_uuid):
        raise ValueError(
            f"LPAR {lpar_selector!r} does not belong to managed system "
            f"{system_uuid}; name the managed system that hosts it, or omit "
            "the selector to have it discovered"
        )


async def _resolve_and_authorize_lpar(
    hmc: HMCClient,
    lpar_name_or_uuid: str,
    system_name_or_uuid: str | None,
    *,
    ownership_override: bool,
) -> str:
    """Resolve one LPAR, authorize the mutation, and return its UUID.

    The guarded counterpart of the resolve chain in :func:`rename_lpar`, for the
    operations whose managed-system selector is optional. Either way the
    partition is established to live on the system whose token the guard reads:
    by discovery when the selector is omitted, by
    :func:`_verify_partition_on_system` when it is supplied (ADR 0092).

    An approved override reads no token, so it skips the fleet walk the guard
    alone requires — otherwise an oversized fleet, a slow one, or an unreachable
    owning frame would *block* the operator's exception on exactly the degraded
    inventory that provokes it (ADR 0092 §4). It still resolves the partition
    name, one REST read, so the audit record for a deliberate bypass of the
    ownership control names the partition in the same vocabulary as every other
    override record.

    A blank *system_name_or_uuid* is read as absent on both branches: MCP
    clients that serialise an unset optional string as ``""`` sent it before
    this operation existed, and it was ignored.
    """
    selector = (system_name_or_uuid or "").strip() or None
    if ownership_override:
        return await _authorize_override(hmc, lpar_name_or_uuid, selector)
    if selector is None:
        lpar_uuid = await resolve_lpar_uuid(hmc, lpar_name_or_uuid)
        # Establish the partition exists *before* the walk: `is_uuid` is a format
        # check, so an unknown or non-partition UUID would otherwise drive up to
        # 100 full partition-feed reads from one caller-supplied string.
        lpar_name = await _partition_name(hmc, lpar_uuid, lpar_name_or_uuid)
        system_uuid, system_name = await _discover_owning_system(
            hmc, lpar_uuid, lpar_name_or_uuid
        )
    else:
        system_uuid = await resolve_system_uuid(hmc, selector)
        lpar_uuid = await resolve_lpar_uuid(
            hmc, lpar_name_or_uuid, system_name_or_uuid=system_uuid
        )
        await _verify_partition_on_system(
            hmc, system_uuid, lpar_uuid, lpar_name_or_uuid
        )
        system_name, lpar_name = await resolve_lpar_ownership_names(
            hmc, system_uuid, selector, lpar_uuid
        )
    await authorize_lpar_mutation(hmc, system_name, lpar_name)
    return lpar_uuid


async def _partition_name(hmc: HMCClient, lpar_uuid: str, lpar_label: str) -> str:
    """Read one partition's CLI name, and fail if the UUID has no partition.

    Two different failures, only one of which this function raises. A UUID that
    names nothing is rejected by the GET itself — ``client._get`` raises
    ``HMCError`` on the 404 and it propagates from here unchanged, naming the
    partition the caller got wrong. The ``ValueError`` below covers the narrower
    case of a resource that reads back but carries no ``PartitionName``.

    Both stop the caller before the ADR 0094 fleet walk, which is the point:
    ``is_uuid`` is a format check, so without this the walk would read up to
    ``MAX_PARENT_DISCOVERY_SYSTEMS`` partition feeds before reporting a missing
    partition as a missing *system*.
    """
    lpar = await hmc.get_logical_partition(lpar_uuid)
    lpar_name = ((lpar or {}).get("Resource") or {}).get("PartitionName")
    if not lpar_name:
        raise ValueError(
            f"No LPAR {lpar_label!r} found. "
            "Use hmc_list_lpars to list available partitions."
        )
    return str(lpar_name)


async def _authorize_override(
    hmc: HMCClient, lpar_name_or_uuid: str, selector: str | None
) -> str:
    """Audit an operator-approved ownership bypass and return the LPAR's UUID."""
    lpar_uuid = await resolve_lpar_uuid(
        hmc, lpar_name_or_uuid, system_name_or_uuid=selector
    )
    lpar_name = await _partition_name(hmc, lpar_uuid, lpar_name_or_uuid)
    await authorize_lpar_mutation(
        hmc, selector or "", lpar_name, ownership_override=True
    )
    return lpar_uuid


async def _apply_dlpar_document(
    hmc: HMCClient,
    lpar_name_or_uuid: str,
    document: str,
    system_name_or_uuid: str | None,
    ownership_override: bool,
) -> dict[str, Any] | None:
    """Authorize one partition, then POST a partial LogicalPartition document."""
    lpar_uuid = await _resolve_and_authorize_lpar(
        hmc,
        lpar_name_or_uuid,
        system_name_or_uuid,
        ownership_override=ownership_override,
    )
    try:
        return await hmc.modify_logical_partition(lpar_uuid, document)
    except HMCError as exc:
        translate_lpar_write_error(exc)
        raise


async def set_lpar_processors(
    hmc: HMCClient,
    system_name_or_uuid: str | None,
    lpar_name_or_uuid: str,
    resources: LparResources,
    *,
    ownership_override: bool = False,
) -> dict[str, Any] | None:
    """Authorize and apply a DLPAR processor change to one partition.

    Posts a minimal ``PartitionProcessorConfiguration`` document: only the
    fields set on *resources* change, and the rest of the partition's processor
    configuration is left alone. For a shared partition ``procs`` are
    processing units (fractional values such as ``0.5`` are valid) and
    ``vcpus`` are virtual processor counts; set ``dedicated=True`` for
    whole-CPU assignment, ``False`` for shared, and leave it unset to keep the
    current sharing mode.

    If the partition has no active RMC connection the change is profile-only
    and takes effect on its next activation; no reboot is triggered either way.

    ADR 0092 §3.2 classifies this as Reconfiguring, so
    :func:`authorize_lpar_mutation` runs unconditionally before the write.
    *system_name_or_uuid* stays optional (ADR 0063): when it is omitted the
    owning managed system is discovered so the guard can still read the token
    (ADR 0094).
    """
    return await _apply_dlpar_document(
        hmc,
        lpar_name_or_uuid,
        build_dlpar_proc_document(resources),
        system_name_or_uuid,
        ownership_override,
    )


async def set_lpar_memory(
    hmc: HMCClient,
    system_name_or_uuid: str | None,
    lpar_name_or_uuid: str,
    resources: LparResources,
    *,
    ownership_override: bool = False,
) -> dict[str, Any] | None:
    """Authorize and apply a DLPAR memory change to one partition.

    Posts a minimal ``PartitionMemoryConfiguration`` document: memory values
    are in MiB, only the fields set on *resources* change, and the processor
    fields of *resources* are ignored.

    If the partition has no active RMC connection the change is profile-only
    and takes effect on its next activation; no reboot is triggered either way.

    ADR 0092 §3.2 classifies this as Reconfiguring, so
    :func:`authorize_lpar_mutation` runs unconditionally before the write.
    *system_name_or_uuid* stays optional (ADR 0063): when it is omitted the
    owning managed system is discovered so the guard can still read the token
    (ADR 0094).
    """
    return await _apply_dlpar_document(
        hmc,
        lpar_name_or_uuid,
        build_dlpar_mem_document(resources),
        system_name_or_uuid,
        ownership_override,
    )


# ====================================================================== #
# LPAR Boot Order Operations
# ====================================================================== #


async def read_lpar_boot_order(
    hmc: HMCClient,
    system_name_or_uuid: str,
    lpar_uuid: str,
) -> dict[str, Any]:
    """Read an LPAR's boot order state (pending and current).

    Returns the boot device order for the LPAR, including both the pending
    boot string (next boot) and the current boot device list.

    Args:
        hmc: HMC client instance.
        system_name_or_uuid: CLI name or UUID of the system.
        lpar_uuid: UUID of the LPAR.

    Returns:
        Dictionary with boot order information containing:
        - lpar_uuid: UUID of the LPAR
        - lpar_name: Name of the LPAR
        - pending_boot_string: The PendingBootString for the next boot
        - boot_device_list: The current BootDeviceList
        - last_booted_device_string: The device used on last boot

    Raises:
        ValueError: If the LPAR cannot be resolved or found.
    """
    lpar = await hmc.get_logical_partition(lpar_uuid)
    if not lpar:
        raise ValueError(f"LPAR {lpar_uuid!r} not found")

    resource = lpar.get("Resource") or {}
    boot_list_info = resource.get("BootListInformation") or {}

    return {
        "lpar_uuid": lpar_uuid,
        "lpar_name": resource.get("PartitionName"),
        "pending_boot_string": boot_list_info.get("PendingBootString"),
        "boot_device_list": boot_list_info.get("BootDeviceList"),
        "last_booted_device_string": boot_list_info.get("LastBootedDeviceString"),
    }


async def set_lpar_boot_order(
    hmc: HMCClient,
    system_name_or_uuid: str,
    lpar_uuid: str,
    devices: list[str],
    *,
    ownership_override: bool = False,
) -> dict[str, Any] | None:
    """Set an LPAR's boot order to a validated device selector list.

    Sets the PendingBootString to an ordered list of boot device selectors.
    Changes take effect on the next LPAR activation (no reboot required).

    Args:
        hmc: HMC client instance.
        system_name_or_uuid: CLI name or UUID of the system.
        lpar_uuid: UUID of the LPAR.
        devices: Ordered list of boot device selectors (cd, disk, network).
        ownership_override: If True, skip ownership token validation.

    Returns:
        Updated LPAR resource if successful, None otherwise.

    Raises:
        ValueError: If device selectors are invalid or LPAR cannot be resolved.
    """
    # Validate device selectors
    for device in devices:
        if device not in BOOT_DEVICE_SELECTORS:
            raise ValueError(
                f"Invalid boot device selector: {device!r}. "
                f"Must be one of: {BOOT_DEVICE_SELECTORS}"
            )

    if not devices:
        raise ValueError("Boot order must contain at least one device")

    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    system_name, lpar_name = await resolve_lpar_ownership_names(
        hmc, system_uuid, system_name_or_uuid, lpar_uuid
    )
    await authorize_lpar_mutation(
        hmc, system_name, lpar_name, ownership_override=ownership_override
    )

    xml = build_boot_order_document(devices)
    try:
        updated = await hmc.modify_logical_partition(lpar_uuid, xml)
    except HMCError as exc:
        translate_lpar_write_error(exc)
        raise

    _logger.info(
        "Set boot order for LPAR %s (%s) to: %s",
        lpar_name,
        lpar_uuid,
        ", ".join(devices),
    )

    return updated


async def clear_lpar_boot_order(
    hmc: HMCClient,
    system_name_or_uuid: str,
    lpar_uuid: str,
    *,
    ownership_override: bool = False,
) -> dict[str, Any] | None:
    """Clear an LPAR's boot order (restore HMC defaults).

    Clears the PendingBootString, restoring the default boot behavior.
    Changes take effect on the next LPAR activation (no reboot required).

    Args:
        hmc: HMC client instance.
        system_name_or_uuid: CLI name or UUID of the system.
        lpar_uuid: UUID of the LPAR.
        ownership_override: If True, skip ownership token validation.

    Returns:
        Updated LPAR resource if successful, None otherwise.

    Raises:
        ValueError: If LPAR cannot be resolved.
    """
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    system_name, lpar_name = await resolve_lpar_ownership_names(
        hmc, system_uuid, system_name_or_uuid, lpar_uuid
    )
    await authorize_lpar_mutation(
        hmc, system_name, lpar_name, ownership_override=ownership_override
    )

    xml = build_clear_boot_order_document()
    try:
        updated = await hmc.modify_logical_partition(lpar_uuid, xml)
    except HMCError as exc:
        translate_lpar_write_error(exc)
        raise

    _logger.info(
        "Cleared boot order for LPAR %s (%s) (restored defaults)",
        lpar_name,
        lpar_uuid,
    )

    return updated
