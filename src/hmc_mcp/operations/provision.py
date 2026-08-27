"""Presentation-neutral LPAR provisioning workflow.

Composes create_logical_partition + add_network_adapter + add_vscsi_adapter +
map_storage_to_lpar + power-on into a single call with a structured per-step
result and an optional dry-run that validates preconditions only.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from ..client import HMCClient
from ..resource_identity import resolve_lpar_uuid, resolve_system_uuid
from ..documents import LparResources, PartitionType, StorageKind
from ..errors import HMCError
from ..jobs import JobOutcome, job_outcome
from ..snapshots.affinity import (
    ProvisionAffinityAssessment,
    assess_post_activation_affinity,
    validate_affinity_request,
)
from .lpar import (
    LparCreation,
    LparCreationResult,
    create_and_stamp_lpar,
    power_lpar,
)
from .lpar_dlpar import _resolve_and_authorize_lpar
from .ssh_network import set_minimum_affinity_policy
from ..ssh.transport import HMCCLIError
from ..ssh.selectors import resolve_ssh_names
from .storage import create_virtual_disk
from ..ssh.affinity import (
    MinimumAffinityPolicy,
    require_minimum_affinity_policy_capability,
    validate_minimum_affinity_policy,
)
from ..ssh.lpar import validate_caller_token
from .assignments import (
    LparPcieAssignments,
    apply_validated_lpar_pcie_assignments,
    assignment_step_names,
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
    return await hmc.add_network_adapter(
        lpar_uuid, port_vlan_id, None, None, False, None
    )


async def _add_vscsi(
    hmc: HMCClient,
    lpar_uuid: str,
    vios_partition_id: int,
    vios_slot: int,
) -> dict[str, Any]:
    await hmc.add_vscsi_adapter(lpar_uuid, vios_partition_id, vios_slot, None)
    return {
        "lpar_uuid": lpar_uuid,
        "vios_partition_id": vios_partition_id,
        "vios_slot": vios_slot,
    }


async def _map_storage(
    hmc: HMCClient, storage: ProvisionStorage, lpar_uuid: str
) -> dict[str, Any]:
    await hmc.map_storage_to_lpar(
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
    system_name_or_uuid: str,
    lpar_uuid: str,
    assessment: ProvisionAffinityAssessment | None,
) -> dict[str, Any] | JobOutcome | None:
    """Activate the partition this workflow just created and stamped.

    ``ownership_override=True`` because the token this leg would authorize
    against is the one the same call stamped moments earlier (ADR 0092
    Consequences). The override keeps the resolution inside ADR 0092 §5's two
    mechanisms — it is audited — rather than adding a call-site-conditional
    guard. With ``authorize_power_operations`` on it spares the SSH ownership
    read, though not the two REST name lookups that precede it; with the
    setting off nothing here runs at all. It also means every successful
    provision emits an ``ownership-override`` audit record once the setting is
    on — ``docs/authorization-audit.md`` records that the event is not
    human-triggered only.
    """
    result = await power_lpar(
        hmc,
        system_name_or_uuid,
        lpar_uuid,
        power_on=True,
        force=True,
        wait=assessment is not None,
        timeout_seconds=assessment.timeout_seconds if assessment else 300,
        poll_interval=assessment.poll_interval if assessment else 5,
        ownership_override=True,
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
    ownership_override: bool = False,
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

    lpar_uuid = await _resolve_and_authorize_lpar(
        hmc,
        lpar_name_or_uuid,
        system_name_or_uuid,
        ownership_override=ownership_override,
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


async def _run_policy_leg(
    steps: list[dict[str, Any]],
    hmc: HMCClient,
    system_name_or_uuid: str,
    lpar_name: str,
    policy: MinimumAffinityPolicy | None,
) -> bool:
    if policy is None:
        return True
    return await _record_hmc_step(
        steps,
        "minimum_affinity_policy",
        set_minimum_affinity_policy(hmc, system_name_or_uuid, lpar_name, policy),
    )


async def _run_network_leg(
    steps: list[dict[str, Any]], hmc: HMCClient, lpar_uuid: str, vlan_id: int
) -> bool:
    return await _record_hmc_step(
        steps, "network", _add_network(hmc, lpar_uuid, vlan_id)
    )


async def _run_assignment_leg(
    steps: list[dict[str, Any]],
    hmc: HMCClient,
    system_name_or_uuid: str,
    lpar_name: str,
    assignments: LparPcieAssignments,
) -> bool:
    result = await apply_validated_lpar_pcie_assignments(
        hmc, system_name_or_uuid, lpar_name, assignments
    )
    steps.extend(_step(item.step, item.status, item.result) for item in result.steps)
    return result.workflow_completed


async def _run_power_leg(
    steps: list[dict[str, Any]],
    hmc: HMCClient,
    system_name_or_uuid: str,
    lpar_uuid: str,
    assessment: ProvisionAffinityAssessment | None,
) -> bool:
    try:
        result = await _power_on(hmc, system_name_or_uuid, lpar_uuid, assessment)
    except (HMCError, ValueError) as exc:
        steps.append(_step("power_on", "error", str(exc)))
        return False
    if isinstance(result, JobOutcome):
        if result.timed_out or result.error is not None:
            message = result.error or (
                "PowerOn did not reach a successful terminal status before timeout"
            )
            steps.append(_step("power_on", "error", message))
            return False
        steps.append(_step("power_on", "ok", asdict(result)))
    else:
        steps.append(_step("power_on", "ok", result))
    return True


async def _run_affinity_leg(
    steps: list[dict[str, Any]],
    hmc: HMCClient,
    assessment: ProvisionAffinityAssessment,
    policy: MinimumAffinityPolicy | None,
) -> tuple[bool, tuple[str, ...]]:
    configured_minimum = policy.min_affinity_score if policy is not None else None
    try:
        result = await assess_post_activation_affinity(
            hmc, assessment, configured_minimum=configured_minimum
        )
    except (HMCError, HMCCLIError) as exc:
        steps.append(_step("affinity_assessment", "error", str(exc)))
        return False, ()
    classification = result.assessment.classification
    serialized_result = asdict(result)
    if classification == "none":
        steps.append(_step("affinity_assessment", "ok", serialized_result))
        return True, ()
    if assessment.response == "fail":
        steps.append(_step("affinity_assessment", "error", serialized_result))
        return False, ()
    steps.append(_step("affinity_assessment", "ok", serialized_result))
    return True, (f"Post-activation affinity assessment: {classification}",)


def _failed_provision_result(
    creation: LparCreationResult,
    created_uuid: str,
    steps: list[dict[str, Any]],
    step_names: list[str],
) -> ProvisionResult:
    _skip_steps(steps, step_names[len(steps) :])
    return _provision_result(creation, created_uuid, steps, False)


async def provision_lpar(
    hmc: HMCClient,
    system_name_or_uuid: str,
    name: str,
    network: ProvisionNetwork,
    storage: ProvisionStorage,
    resources: LparResources,
    *,
    partition_type: PartitionType = "AIX/Linux",
    power_on: bool = True,
    dry_run: bool = False,
    assignments: LparPcieAssignments = LparPcieAssignments(),
    caller_token: str | None = None,
    minimum_affinity_policy: MinimumAffinityPolicy | None = None,
    affinity_assessment: ProvisionAffinityAssessment | None = None,
) -> ProvisionResult:
    """Provision an LPAR after validating every requested dependency.

    ``dry_run`` performs only the precondition checks and marks each planned
    step ``dry_run``. On partial failure, completed, failed, and remaining
    steps are reported as ``ok``, ``error``, and ``skipped`` respectively;
    no automatic rollback is attempted.

    Ownership stamping is best effort. ``ownership_stamped`` distinguishes a
    confirmed write, a failed write, and a dry run where no stamp was
    attempted. When ``caller_token`` is supplied, it is written together with
    the ownership stamp, so the result describes both values as one write.
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
    if minimum_affinity_policy is not None:
        validate_minimum_affinity_policy(minimum_affinity_policy)
    if affinity_assessment is not None:
        configured_minimum = (
            minimum_affinity_policy.min_affinity_score
            if minimum_affinity_policy is not None
            else None
        )
        validate_affinity_request(affinity_assessment, configured_minimum)
    if caller_token is not None:
        # First statement, before any HMC round trip: the public operation is
        # reachable directly (api.__all__) without the MCP tool's entry check,
        # and a malformed token must fail identically there (ADR 0064).
        validate_caller_token(caller_token)

    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    if minimum_affinity_policy is not None:
        system_name, _ = await resolve_ssh_names(hmc.config, system_name_or_uuid, None)
        assert system_name is not None
        await require_minimum_affinity_policy_capability(hmc.config, system_name)

    await _check_name_unique(hmc, name)
    await _check_vlan_exists(hmc, system_uuid, network.port_vlan_id)
    if storage.vg_uuid is not None:
        await _check_vg_exists(hmc, storage.vios_uuid, storage.vg_uuid)
    await prevalidate_lpar_pcie_assignments(hmc, system_name_or_uuid, assignments)

    step_names = ["create"]
    if minimum_affinity_policy is not None:
        step_names.append("minimum_affinity_policy")
    step_names.extend(
        ["network", "vscsi", "storage", *assignment_step_names(assignments)]
    )
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

    if not await _run_policy_leg(
        steps,
        hmc,
        system_name_or_uuid,
        name,
        minimum_affinity_policy,
    ):
        return _failed_provision_result(creation, created_uuid, steps, step_names)

    if not await _run_network_leg(steps, hmc, created_uuid, network.port_vlan_id):
        return _failed_provision_result(creation, created_uuid, steps, step_names)

    storage_steps, storage_completed = await _run_storage_leg(
        hmc,
        created_uuid,
        storage,
        vios_partition_id=network.vios_partition_id,
        vios_slot=network.vios_slot,
    )
    steps.extend(storage_steps)
    if not storage_completed:
        return _failed_provision_result(creation, created_uuid, steps, step_names)

    if not await _run_assignment_leg(
        steps, hmc, system_name_or_uuid, name, assignments
    ):
        return _failed_provision_result(creation, created_uuid, steps, step_names)

    if power_on and not await _run_power_leg(
        steps, hmc, system_name_or_uuid, created_uuid, affinity_assessment
    ):
        return _failed_provision_result(creation, created_uuid, steps, step_names)

    if affinity_assessment is not None:
        if not power_on:
            steps.append(_step("affinity_assessment", "skipped"))
            return _provision_result(creation, created_uuid, steps, False)
        completed, warnings = await _run_affinity_leg(
            steps, hmc, affinity_assessment, minimum_affinity_policy
        )
        if not completed:
            return _provision_result(creation, created_uuid, steps, False)
        return _provision_result(creation, created_uuid, steps, True, warnings)

    return _provision_result(creation, created_uuid, steps, True)
