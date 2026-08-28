"""Shared LPAR creation and ownership operations."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from hmc_mcp.operations.affinity import (
    LparAffinityAssessmentOutcome,
    affinity_not_measured,
)

from ...client import HMCClient
from ...resource_identity import is_uuid, resolve_lpar_uuid, resolve_system_uuid
from ...documents import (
    Keylock,
    LparResources,
    OsType,
    PartitionType,
    build_lpar_document,
)
from ...errors import HMCError
from ...jobs import (
    DEFAULT_JOB_POLL_INTERVAL,
    DEFAULT_JOB_TIMEOUT_SECONDS,
    SUCCESSFUL_JOB_STATUSES,
    job_outcome,
    power_off_lpar_job,
    power_on_lpar_job,
    validate_wait_timing,
    wait_for_submitted_job,
)
from hmc_mcp.operations.ownership import stamp_created_lpar_ownership
from hmc_mcp.operations.ownership import resolve_and_authorize_lpar_mutation
from ...ssh.transport import HMCCLIError
from ...ssh.lpar import (
    resolve_system_cli_name,
    create_lpar_via_cli,
    validate_caller_token,
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


async def list_lpars(
    hmc: HMCClient,
    system_name_or_uuid: str | None = None,
    state: PartitionState | None = None,
) -> list[dict[str, Any]]:
    """List LPARs, optionally scoped to one system or one partition state."""
    if system_name_or_uuid is not None and state is not None:
        raise ValueError("Provide at most one of system_name_or_uuid or state")
    if state is not None:
        if state not in PARTITION_STATES:
            allowed = ", ".join(sorted(PARTITION_STATES))
            raise ValueError(f"state must be one of: {allowed}")
        return await hmc.search_uom("LogicalPartition", "PartitionState", state)
    system_uuid = (
        await resolve_system_uuid(hmc, system_name_or_uuid)
        if system_name_or_uuid is not None
        else None
    )
    return await hmc.list_logical_partitions(system_uuid)


async def get_lpar(
    hmc: HMCClient,
    lpar_name_or_uuid: str,
    *,
    system_name_or_uuid: str | None = None,
) -> dict[str, Any] | None:
    """Get one LPAR by UUID or by an optionally system-scoped exact name."""
    if is_uuid(lpar_name_or_uuid):
        return await hmc.get_logical_partition(lpar_name_or_uuid)
    system_uuid = (
        await resolve_system_uuid(hmc, system_name_or_uuid)
        if system_name_or_uuid is not None
        else None
    )
    return await hmc.find_partition_by_name(
        lpar_name_or_uuid, system_uuid=system_uuid
    )


async def get_lpar_state(
    hmc: HMCClient,
    lpar_name_or_uuid: str,
    *,
    system_name_or_uuid: str | None = None,
) -> str | None:
    """Return the current state of one system-scoped LPAR selector."""
    lpar_uuid = await resolve_lpar_uuid(
        hmc, lpar_name_or_uuid, system_name_or_uuid=system_name_or_uuid
    )
    return await hmc.get_quick_property(
        "LogicalPartition", lpar_uuid, "PartitionState"
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
            system_name = await resolve_system_cli_name(hmc.config, system_uuid)
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
    lpar_uuid = await resolve_and_authorize_lpar_mutation(
        hmc,
        lpar_name_or_uuid,
        system_name_or_uuid,
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
    :func:`resolve_and_authorize_lpar_mutation`, shared with the DLPAR operations —
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
        lpar_uuid = await resolve_and_authorize_lpar_mutation(
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
    lpar_uuid = await resolve_and_authorize_lpar_mutation(
        hmc,
        lpar_name_or_uuid,
        system_name_or_uuid,
        ownership_override=ownership_override,
    )
    updated = await hmc.modify_logical_partition(
        lpar_uuid, build_lpar_document(name=new_name)
    )
    return lpar_uuid, updated
