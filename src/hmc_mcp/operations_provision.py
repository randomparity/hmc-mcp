"""Presentation-neutral LPAR provisioning workflow.

Composes create_logical_partition + add_network_adapter + add_vscsi_adapter +
map_storage_to_lpar + power-on into a single call with a structured per-step
result and an optional dry-run that validates preconditions only.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from hmc_mcp.affinity_assessment import (
    AffinityAssessmentInput,
    CapturedPolicyState,
    assess_affinity,
)

from .client import HMCClient
from .common import resolve_lpar_uuid, resolve_system_uuid
from .documents import LparResources, PartitionType, StorageKind
from .errors import HMCError
from .jobs import JobOutcome, job_outcome
from .operations_adapters import add_network_adapter, add_vios_adapter
from .operations_lpar import (
    LparCreation,
    LparCreationResult,
    create_and_stamp_lpar,
    power_lpar,
)
from .operations_ssh_network import set_minimum_affinity_policy
from .operations_ssh_network import (
    get_lpar_memopt_score,
    get_minimum_affinity_policy,
    plan_lpar_memopt_scores,
)
from .ssh import HMCCLIError
from .ssh_selectors import resolve_ssh_names
from .operations_storage import create_virtual_disk, map_storage
from .ssh_commands import (
    MinimumAffinityPolicy,
    require_minimum_affinity_policy_capability,
    validate_caller_token,
    validate_minimum_affinity_policy,
)
from .operations_assignments import (
    LparPcieAssignments,
    _apply_validated_lpar_pcie_assignments,
    prevalidate_lpar_pcie_assignments,
)


@dataclass(frozen=True)
class ProvisionNetwork:
    """Virtual Ethernet and vSCSI attachment inputs."""

    port_vlan_id: int = field(
        metadata={
            "description": "VLAN identifier for the client virtual Ethernet adapter."
        }
    )
    vios_partition_id: int = field(
        metadata={"description": "Partition ID of the VIOS that serves storage."}
    )
    vios_slot: int = field(
        metadata={"description": "Virtual slot number for the VIOS-side vSCSI adapter."}
    )


@dataclass(frozen=True)
class ProvisionStorage:
    """VIOS-backed storage mapping inputs."""

    vios_uuid: str = field(
        metadata={"description": "UUID of the VIOS that owns the storage."}
    )
    storage_name: str = field(
        metadata={"description": "Physical-volume or virtual-disk name to map."}
    )
    kind: StorageKind = field(
        default="VirtualDisk", metadata={"description": "Storage resource kind to map."}
    )
    vg_uuid: str | None = field(
        default=None,
        metadata={
            "description": "Volume-group UUID used when creating a virtual disk."
        },
    )


@dataclass(frozen=True)
class ProvisionAffinityAssessment:
    """Caller-owned captured evidence and post-activation response policy."""

    system_name_or_uuid: str = field(
        metadata={"description": "Captured managed-system identity; must match target."}
    )
    lpar_name: str = field(
        metadata={"description": "Captured LPAR name; must match requested name."}
    )
    captured_score: int | None = field(
        metadata={"description": "Previously observed LPAR affinity score."}
    )
    captured_policy_state: CapturedPolicyState = field(
        metadata={"description": "Capability and policy state at capture time."}
    )
    captured_minimum: int | None = field(
        metadata={"description": "Minimum affinity score observed at capture time."}
    )
    captured_at: datetime = field(
        metadata={"description": "Timezone-aware timestamp for captured evidence."}
    )
    stale_after_seconds: int = field(
        metadata={"description": "Maximum accepted age of captured evidence."}
    )
    response: Literal["warn", "fail"] = field(
        metadata={"description": "Explicit response to an adverse assessment."}
    )
    regression_threshold: int | None = field(
        default=None,
        metadata={"description": "Caller-owned maximum acceptable score regression."},
    )
    optimization_threshold: int | None = field(
        default=None,
        metadata={"description": "Caller-owned minimum worthwhile predicted gain."},
    )
    timeout_seconds: int = field(
        default=300,
        metadata={"description": "Maximum seconds to wait for PowerOn completion."},
    )
    poll_interval: int = field(
        default=5,
        metadata={"description": "Seconds between PowerOn job status reads."},
    )


@dataclass(frozen=True)
class ProvisionResult:
    """Truthful outcome of a provisioning attempt."""

    resource_created: bool = field(
        metadata={"description": "Whether this call created the LPAR."}
    )
    workflow_completed: bool = field(
        metadata={"description": "Whether every requested workflow step completed."}
    )
    lpar_uuid: str | None = field(
        metadata={"description": "Created LPAR UUID, or null before creation."}
    )
    dry_run: bool = field(
        metadata={"description": "Whether the call only validated preconditions."}
    )
    ownership_stamped: bool | None = field(
        metadata={
            "description": "Ownership-token stamp result, or null when not attempted."
        }
    )
    steps: tuple[dict[str, Any], ...] = field(
        metadata={"description": "Ordered per-step status and result records."}
    )
    warnings: tuple[str, ...] = field(
        metadata={"description": "Non-fatal workflow warnings."}
    )


@dataclass(frozen=True)
class AttachDiskResult:
    """Truthful outcome of attaching a new disk to an existing LPAR."""

    workflow_completed: bool
    lpar_uuid: str
    dry_run: bool
    steps: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]


# ---------------------------------------------------------------------- #
# Precondition helpers
# ---------------------------------------------------------------------- #


async def _check_name_unique(hmc, name: str) -> None:
    """Raise ValueError if an LPAR with *name* already exists."""
    existing = await hmc.find_partition_by_name(name)
    if existing:
        raise ValueError(
            f"An LPAR named {name!r} already exists "
            f"(UUID {existing.get('UUID')!r}). Choose a different name "
            "or delete the existing partition first."
        )


async def _check_vlan_exists(hmc, system_uuid: str, port_vlan_id: int) -> None:
    """Raise ValueError if no VirtualNetwork with *port_vlan_id* exists."""
    networks = await hmc.list_virtual_networks(system_uuid)
    malformed: list[str] = []
    for net in networks:
        res = net.get("Resource") or {}
        vlan = res.get("NetworkVLANID")
        if vlan is None:
            continue
        try:
            parsed_vlan = int(vlan)
        except (TypeError, ValueError):
            identity = res.get("NetworkName") or net.get("UUID") or "unknown network"
            malformed.append(f"{identity!r} has NetworkVLANID {vlan!r}")
            continue
        if parsed_vlan == port_vlan_id:
            return
    malformed_note = (
        f" Ignored malformed network records: {', '.join(malformed)}."
        if malformed
        else ""
    )
    raise ValueError(
        f"No VirtualNetwork with VLAN ID {port_vlan_id} found on system "
        f"{system_uuid!r}. List virtual networks to inspect available VLANs."
        f"{malformed_note}"
    )


async def _check_vg_exists(hmc, vios_uuid: str, vg_uuid: str) -> None:
    """Raise ValueError if no VolumeGroup with *vg_uuid* exists on *vios_uuid*."""
    vgs = await hmc.list_volume_groups(vios_uuid)
    found = any(vg.get("UUID") == vg_uuid for vg in vgs)
    if not found:
        raise ValueError(
            f"VolumeGroup {vg_uuid!r} not found on VIOS {vios_uuid!r}. "
            "List volume groups to inspect the available groups."
        )


# ---------------------------------------------------------------------- #
# Step runner
# ---------------------------------------------------------------------- #


def _step(name: str, status: str, result: Any = None) -> dict[str, Any]:
    """Build a single step-result dict."""
    entry: dict[str, Any] = {"step": name, "status": status}
    if result is not None:
        entry["result"] = result
    return entry


async def _record_hmc_step(
    steps: list[dict[str, Any]], name: str, operation: Awaitable[Any]
) -> bool:
    """Record an expected HMC operation failure and propagate code defects."""
    try:
        result = await operation
    except HMCError as exc:
        steps.append(_step(name, "error", str(exc)))
        return False
    steps.append(_step(name, "ok", result))
    return True


async def _add_network(
    hmc: HMCClient, lpar_uuid: str, port_vlan_id: int
) -> dict[str, Any] | None:
    result = await add_network_adapter(
        hmc, lpar_uuid, port_vlan_id, None, None, False, None
    )
    return result.resource


async def _add_vscsi(
    hmc: HMCClient,
    lpar_uuid: str,
    vios_partition_id: int,
    vios_slot: int,
) -> dict[str, Any]:
    await add_vios_adapter(
        hmc,
        lpar_uuid,
        vios_partition_id,
        vios_slot,
        None,
        fibre_channel=False,
    )
    return {
        "lpar_uuid": lpar_uuid,
        "vios_partition_id": vios_partition_id,
        "vios_slot": vios_slot,
    }


async def _map_storage(
    hmc: HMCClient, storage: ProvisionStorage, lpar_uuid: str
) -> dict[str, Any]:
    await map_storage(
        hmc,
        storage.vios_uuid,
        storage.kind,
        storage.storage_name,
        lpar_uuid,
        None,
    )
    return {
        "lpar_uuid": lpar_uuid,
        "vios_uuid": storage.vios_uuid,
        "storage_name": storage.storage_name,
    }


async def _create_disk(
    hmc: HMCClient, storage: ProvisionStorage, capacity_mib: int
) -> dict[str, Any]:
    assert storage.vg_uuid is not None
    await create_virtual_disk(
        hmc,
        storage.vios_uuid,
        storage.vg_uuid,
        storage.storage_name,
        capacity_mib,
    )
    return {"disk_name": storage.storage_name, "capacity_mb": capacity_mib}


async def _power_on(
    hmc: HMCClient,
    lpar_uuid: str,
    assessment: ProvisionAffinityAssessment | None,
) -> dict[str, Any] | JobOutcome | None:
    result = await power_lpar(
        hmc,
        lpar_uuid,
        power_on=True,
        force=True,
        wait=assessment is not None,
        timeout_seconds=assessment.timeout_seconds if assessment else 300,
        poll_interval=assessment.poll_interval if assessment else 5,
    )
    if assessment is None:
        return result.job
    return job_outcome("PowerOn", result.job)


def _skip_steps(steps: list[dict[str, Any]], names: list[str]) -> None:
    steps.extend(_step(name, "skipped") for name in names)


async def _run_storage_leg(
    hmc: HMCClient,
    lpar_uuid: str,
    storage: ProvisionStorage,
    *,
    vios_partition_id: int,
    vios_slot: int,
    disk_capacity_mib: int | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Run the shared ordered vSCSI storage workflow."""
    steps: list[dict[str, Any]] = []
    operations: list[tuple[str, Callable[[], Awaitable[Any]]]] = []
    if disk_capacity_mib is not None:
        operations.append(
            ("create_disk", lambda: _create_disk(hmc, storage, disk_capacity_mib))
        )
    operations.extend(
        (
            (
                "vscsi",
                lambda: _add_vscsi(hmc, lpar_uuid, vios_partition_id, vios_slot),
            ),
            ("storage", lambda: _map_storage(hmc, storage, lpar_uuid)),
        )
    )
    for index, (name, operation) in enumerate(operations):
        if not await _record_hmc_step(steps, name, operation()):
            _skip_steps(steps, [step_name for step_name, _ in operations[index + 1 :]])
            return steps, False
    return steps, True


async def attach_disk_to_lpar(
    hmc: HMCClient,
    lpar_name_or_uuid: str,
    storage: ProvisionStorage,
    *,
    capacity_mib: int,
    vios_partition_id: int,
    vios_slot: int,
    dry_run: bool = False,
    system_name_or_uuid: str | None = None,
) -> AttachDiskResult:
    """Create and attach a virtual disk to an existing LPAR."""
    if capacity_mib <= 0:
        raise ValueError("capacity_mib must be greater than zero")
    if storage.kind != "VirtualDisk" or storage.vg_uuid is None:
        raise ValueError("disk attachment requires a VirtualDisk with vg_uuid")

    lpar_uuid = await resolve_lpar_uuid(
        hmc, lpar_name_or_uuid, system_name_or_uuid=system_name_or_uuid
    )
    await _check_vg_exists(hmc, storage.vios_uuid, storage.vg_uuid)
    step_names = ["create_disk", "vscsi", "storage"]
    if dry_run:
        return AttachDiskResult(
            False,
            lpar_uuid,
            True,
            tuple(_step(name, "dry_run") for name in step_names),
            (),
        )

    steps, completed = await _run_storage_leg(
        hmc,
        lpar_uuid,
        storage,
        vios_partition_id=vios_partition_id,
        vios_slot=vios_slot,
        disk_capacity_mib=capacity_mib,
    )
    return AttachDiskResult(completed, lpar_uuid, False, tuple(steps), ())


def _provision_result(
    creation: LparCreationResult | None,
    created_uuid: str | None,
    steps: list[dict[str, Any]],
    workflow_completed: bool,
    warnings: tuple[str, ...] = (),
) -> ProvisionResult:
    return ProvisionResult(
        resource_created=creation.resource_created if creation else False,
        workflow_completed=workflow_completed,
        lpar_uuid=created_uuid,
        dry_run=False,
        ownership_stamped=creation.ownership_stamped if creation else None,
        steps=tuple(steps),
        warnings=(*(creation.warnings if creation else ()), *warnings),
    )


def _score(row: dict[str, object], field_name: str) -> int | None:
    value = row.get(field_name)
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _validate_affinity_request(request: ProvisionAffinityAssessment) -> None:
    """Validate every caller-controlled assessment value without HMC traffic."""
    policy_state: Literal["configured", "absent", "unsupported"] = (
        request.captured_policy_state
        if request.captured_policy_state in {"configured", "absent"}
        else "unsupported"
    )
    assess_affinity(
        AffinityAssessmentInput(
            captured_score=request.captured_score,
            current_score=request.captured_score,
            predicted_score=request.captured_score,
            policy_state=policy_state,
            captured_policy_state=request.captured_policy_state,
            configured_minimum=request.captured_minimum,
            captured_minimum=request.captured_minimum,
            captured_at=request.captured_at,
            assessed_at=request.captured_at,
            stale_after_seconds=request.stale_after_seconds,
            regression_threshold=request.regression_threshold,
            optimization_threshold=request.optimization_threshold,
        )
    )


async def _assess_post_activation_affinity(
    hmc: HMCClient,
    system: str,
    lpar: str,
    request: ProvisionAffinityAssessment,
    applied_policy: MinimumAffinityPolicy | None,
) -> dict[str, Any]:
    current_row = await get_lpar_memopt_score(hmc.config, system, lpar)
    predicted_rows = await plan_lpar_memopt_scores(hmc.config, system)
    predicted_row = next(
        (row for row in predicted_rows if row.get("lpar_name") == lpar), None
    )
    predicted_score = (
        _score(predicted_row, "predicted_lpar_score") if predicted_row else None
    )
    if applied_policy is None:
        policy = await get_minimum_affinity_policy(hmc.config, system, lpar)
        if policy.capability == "capability-unavailable":
            policy_state: Literal["configured", "absent", "unsupported"] = "unsupported"
        elif policy.min_affinity_score is not None:
            policy_state = "configured"
        else:
            policy_state = "absent"
        configured_minimum = policy.min_affinity_score
    else:
        policy_state = "configured"
        configured_minimum = applied_policy.min_affinity_score
    assessment = assess_affinity(
        AffinityAssessmentInput(
            captured_score=request.captured_score,
            current_score=_score(current_row, "curr_lpar_score"),
            predicted_score=predicted_score,
            policy_state=policy_state,
            captured_policy_state=request.captured_policy_state,
            configured_minimum=configured_minimum,
            captured_minimum=request.captured_minimum,
            captured_at=request.captured_at,
            assessed_at=datetime.now(UTC),
            stale_after_seconds=request.stale_after_seconds,
            regression_threshold=request.regression_threshold,
            optimization_threshold=request.optimization_threshold,
        )
    )
    return {
        "assessment": asdict(assessment),
        "achieved_score": assessment.evidence.current_score,
        "predicted_score": assessment.evidence.predicted_score,
        "prediction_guaranteed": False,
    }


# ---------------------------------------------------------------------- #
# Operation
# ---------------------------------------------------------------------- #


async def provision_lpar(
    hmc: HMCClient,
    system_name_or_uuid: str,
    name: str,
    network: ProvisionNetwork,
    storage: ProvisionStorage,
    resources: LparResources,
    partition_type: PartitionType = "AIX/Linux",
    power_on: bool = True,
    dry_run: bool = False,
    assignments: LparPcieAssignments = LparPcieAssignments(),
    caller_token: str | None = None,
    minimum_affinity_policy: MinimumAffinityPolicy | None = None,
    affinity_assessment: ProvisionAffinityAssessment | None = None,
) -> ProvisionResult:
    """Provision a new LPAR end-to-end: create, add network adapter, add vSCSI
    adapter, map disk storage, and power on — in a single call.

    **Always validates preconditions first** (name uniqueness, VLAN existence,
    volume-group existence). Pass ``dry_run=True`` to run *only* the
    precondition checks without creating anything; the result will show each
    step as ``{"status": "dry_run"}``.

    On partial failure the completed steps are reported as ``"ok"``, the
    failed step as ``"error"``, and remaining steps as ``"skipped"``.
    No automatic rollback is performed — clean up manually with
    Delete the partition or its adapters manually as appropriate.

    Parameters
    ----------
    system_name_or_uuid:
        Target managed system — either a SystemName or UUID.
    name:
        Name for the new LPAR. Must be unique across the HMC.
    network:
        Virtual Ethernet VLAN and VIOS vSCSI attachment inputs.
    storage:
        VIOS-backed storage mapping inputs, including optional volume-group
        validation.
    resources:
        Memory and processor bounds for the new partition.
    partition_type:
        Partition type: ``"AIX/Linux"`` (default), ``"OS400"``, or
        ``"Virtual IO Server"``.
    power_on:
        Submit a PowerOn job after provisioning (default ``True``).
    dry_run:
        When ``True``, run precondition checks only — no LPAR is created.
    caller_token:
        Optional caller tracking reference embedded in the partition
        description as ``[caller <token>]`` after the ownership stamp
        (ADR 0064); 1–64 printable ASCII characters, no whitespace or
        comma, equals, double quote, square brackets, or backslash.

    Returns
    -------
    ProvisionResult with:
    - ``resource_created``: whether the create operation succeeded.
    - ``workflow_completed``: whether every requested step succeeded.
    - ``lpar_uuid``: the validated UUID needed for follow-on operations.
    - ``dry_run`` (bool): mirrors the input flag.
    - ``steps`` (list): per-step result dicts ``{step, status, result?}``.
      status is ``"ok"``, ``"error"``, ``"skipped"``, or ``"dry_run"``.
    - ``warnings`` (list): non-fatal notices; includes ownership stamp failures
      or skips when the stamp could not be applied after creation.
    - ``ownership_stamped`` (bool | None): ``True`` when the description-field
      ownership token was written; ``False`` when the SSH stamp attempt failed;
      ``None`` when the stamp was not attempted. With ``caller_token``,
      ``True`` confirms both the ownership stamp and the caller segment
      landed (one combined write); ``False`` means both were lost.
    """

    if affinity_assessment is not None:
        if (
            affinity_assessment.system_name_or_uuid != system_name_or_uuid
            or affinity_assessment.lpar_name != name
        ):
            raise ValueError(
                "affinity assessment identities must match the provisioned system and LPAR"
            )
        if affinity_assessment.response not in {"warn", "fail"}:
            raise ValueError("affinity assessment response must be warn or fail")
        if affinity_assessment.timeout_seconds < 0:
            raise ValueError("affinity assessment timeout_seconds must be non-negative")
        if affinity_assessment.poll_interval <= 0:
            raise ValueError("affinity assessment poll_interval must be positive")
        _validate_affinity_request(affinity_assessment)
    if minimum_affinity_policy is not None:
        validate_minimum_affinity_policy(minimum_affinity_policy)
    if caller_token is not None:
        # First statement, before any HMC round trip: the public operation is
        # reachable directly (api.__all__) without the MCP tool's entry check,
        # and a malformed token must fail identically there (ADR 0064).
        validate_caller_token(caller_token)

    # ----------------------------------------------------------------
    # 1. Resolve system UUID
    # ----------------------------------------------------------------
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    if minimum_affinity_policy is not None:
        system_name, _ = await resolve_ssh_names(hmc.config, system_name_or_uuid, None)
        assert system_name is not None
        await require_minimum_affinity_policy_capability(hmc.config, system_name)

    # ----------------------------------------------------------------
    # 2. Preconditions (always, including dry-run)
    # ----------------------------------------------------------------
    if dry_run:
        await _check_name_unique(hmc, name)
    await _check_vlan_exists(hmc, system_uuid, network.port_vlan_id)
    if storage.vg_uuid is not None:
        await _check_vg_exists(hmc, storage.vios_uuid, storage.vg_uuid)
    await prevalidate_lpar_pcie_assignments(hmc, system_name_or_uuid, assignments)

    # ----------------------------------------------------------------
    # 3. Dry-run exit
    # ----------------------------------------------------------------
    assignment_names = [
        *(f"dedicated[{index}]" for index, _ in enumerate(assignments.dedicated)),
        *(f"sriov[{index}]" for index, _ in enumerate(assignments.sriov)),
        *(f"vnic[{index}]" for index, _ in enumerate(assignments.vnics)),
    ]
    step_names = ["create"]
    if minimum_affinity_policy is not None:
        step_names.append("minimum_affinity_policy")
    step_names.extend(["network", "vscsi", "storage", *assignment_names])
    if power_on:
        step_names.append("power_on")
    if affinity_assessment is not None:
        step_names.append("affinity_assessment")

    if dry_run:
        return ProvisionResult(
            False,
            False,
            None,
            True,
            None,
            tuple(_step(n, "dry_run") for n in step_names),
            (),
        )

    steps: list[dict[str, Any]] = []
    try:
        creation = await create_and_stamp_lpar(
            hmc,
            system_name_or_uuid,
            LparCreation(name, partition_type, resources, caller_token=caller_token),
        )
    except HMCError as exc:
        steps.append(_step("create", "error", str(exc)))
        _skip_steps(steps, step_names[1:])
        return _provision_result(None, None, steps, False)

    created_lpar = creation.lpar
    created_uuid = (created_lpar or {}).get("UUID")
    if not isinstance(created_uuid, str) or not created_uuid:
        steps.append(_step("create", "error", "LPAR creation returned no UUID"))
        _skip_steps(steps, step_names[1:])
        return _provision_result(creation, None, steps, False)
    steps.append(_step("create", "ok", created_lpar))

    if minimum_affinity_policy is not None:
        if not await _record_hmc_step(
            steps,
            "minimum_affinity_policy",
            set_minimum_affinity_policy(
                hmc,
                system_name_or_uuid,
                name,
                minimum_affinity_policy,
            ),
        ):
            _skip_steps(steps, step_names[2:])
            return _provision_result(creation, created_uuid, steps, False)

    if not await _record_hmc_step(
        steps,
        "network",
        _add_network(hmc, created_uuid, network.port_vlan_id),
    ):
        network_index = step_names.index("network")
        _skip_steps(steps, step_names[network_index + 1 :])
        return _provision_result(creation, created_uuid, steps, False)

    storage_steps, storage_completed = await _run_storage_leg(
        hmc,
        created_uuid,
        storage,
        vios_partition_id=network.vios_partition_id,
        vios_slot=network.vios_slot,
    )
    steps.extend(storage_steps)
    if not storage_completed:
        _skip_steps(steps, [*assignment_names, *(["power_on"] if power_on else [])])
        return _provision_result(creation, created_uuid, steps, False)

    assignment_result = await _apply_validated_lpar_pcie_assignments(
        hmc, system_name_or_uuid, name, assignments
    )
    steps.extend(
        _step(item.step, item.status, item.result) for item in assignment_result.steps
    )
    if not assignment_result.workflow_completed:
        if power_on:
            _skip_steps(steps, ["power_on"])
        return _provision_result(creation, created_uuid, steps, False)

    if power_on:
        try:
            power_result = await _power_on(hmc, created_uuid, affinity_assessment)
        except HMCError as exc:
            steps.append(_step("power_on", "error", str(exc)))
            if affinity_assessment is not None:
                steps.append(_step("affinity_assessment", "skipped"))
            return _provision_result(creation, created_uuid, steps, False)
        if isinstance(power_result, JobOutcome):
            if power_result.timed_out or power_result.error is not None:
                message = power_result.error or (
                    "PowerOn did not reach a successful terminal status before timeout"
                )
                steps.append(_step("power_on", "error", message))
                steps.append(_step("affinity_assessment", "skipped"))
                return _provision_result(creation, created_uuid, steps, False)
            steps.append(_step("power_on", "ok", asdict(power_result)))
        else:
            steps.append(_step("power_on", "ok", power_result))

    if affinity_assessment is not None:
        if not power_on:
            steps.append(_step("affinity_assessment", "skipped"))
            return _provision_result(creation, created_uuid, steps, False)
        try:
            result = await _assess_post_activation_affinity(
                hmc,
                system_name_or_uuid,
                name,
                affinity_assessment,
                minimum_affinity_policy,
            )
        except (HMCError, HMCCLIError) as exc:
            steps.append(_step("affinity_assessment", "error", str(exc)))
            return _provision_result(creation, created_uuid, steps, False)
        classification = result["assessment"]["classification"]
        if classification != "none":
            warning = f"Post-activation affinity assessment: {classification}"
            if affinity_assessment.response == "fail":
                steps.append(_step("affinity_assessment", "error", result))
                return _provision_result(creation, created_uuid, steps, False)
            steps.append(_step("affinity_assessment", "ok", result))
            return _provision_result(creation, created_uuid, steps, True, (warning,))
        steps.append(_step("affinity_assessment", "ok", result))

    return _provision_result(creation, created_uuid, steps, True)
