"""Presentation-neutral LPAR provisioning workflow.

Composes create_logical_partition + add_network_adapter + add_vscsi_adapter +
map_storage_to_lpar + power-on into a single call with a structured per-step
result and an optional dry-run that validates preconditions only.
"""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any

from .client import HMCClient
from .common import resolve_system_uuid
from .documents import LparResources, PartitionType, StorageKind
from .errors import HMCError
from .jobs import power_on_lpar_job
from .operations_lpar import (
    LparCreation,
    LparCreationResult,
    create_and_stamp_lpar,
)


@dataclass(frozen=True)
class ProvisionNetwork:
    """Virtual Ethernet and vSCSI attachment inputs."""

    port_vlan_id: int
    vios_partition_id: int
    vios_slot: int


@dataclass(frozen=True)
class ProvisionStorage:
    """VIOS-backed storage mapping inputs."""

    vios_uuid: str
    storage_name: str
    kind: StorageKind = "VirtualDisk"
    vg_uuid: str | None = None


@dataclass(frozen=True)
class ProvisionResult:
    """Truthful outcome of a provisioning attempt."""

    resource_created: bool
    workflow_completed: bool
    lpar_uuid: str | None
    dry_run: bool
    ownership_stamped: bool | None
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
        f"{system_uuid!r}. Use hmc_list_virtual_networks to list available VLANs."
        f"{malformed_note}"
    )


async def _check_vg_exists(hmc, vios_uuid: str, vg_uuid: str) -> None:
    """Raise ValueError if no VolumeGroup with *vg_uuid* exists on *vios_uuid*."""
    vgs = await hmc.list_volume_groups(vios_uuid)
    found = any(vg.get("UUID") == vg_uuid for vg in vgs)
    if not found:
        raise ValueError(
            f"VolumeGroup {vg_uuid!r} not found on VIOS {vios_uuid!r}. "
            "Use hmc_list_volume_groups to list available volume groups."
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


def _skip_steps(steps: list[dict[str, Any]], names: list[str]) -> None:
    steps.extend(_step(name, "skipped") for name in names)


def _provision_result(
    creation: LparCreationResult | None,
    created_uuid: str | None,
    steps: list[dict[str, Any]],
    workflow_completed: bool,
) -> ProvisionResult:
    return ProvisionResult(
        resource_created=creation.resource_created if creation else False,
        workflow_completed=workflow_completed,
        lpar_uuid=created_uuid,
        dry_run=False,
        ownership_stamped=creation.ownership_stamped if creation else None,
        steps=tuple(steps),
        warnings=creation.warnings if creation else (),
    )


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
    ``hmc_delete_lpar`` / ``hmc_delete_adapter`` as appropriate.

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
      ``None`` when the stamp was not attempted.
    """

    # ----------------------------------------------------------------
    # 1. Resolve system UUID
    # ----------------------------------------------------------------
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)

    # ----------------------------------------------------------------
    # 2. Preconditions (always, including dry-run)
    # ----------------------------------------------------------------
    await _check_name_unique(hmc, name)
    await _check_vlan_exists(hmc, system_uuid, network.port_vlan_id)
    if storage.vg_uuid is not None:
        await _check_vg_exists(hmc, storage.vios_uuid, storage.vg_uuid)

    # ----------------------------------------------------------------
    # 3. Dry-run exit
    # ----------------------------------------------------------------
    step_names = ["create", "network", "vscsi", "storage"]
    if power_on:
        step_names.append("power_on")

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
            system_uuid,
            system_name_or_uuid,
            LparCreation(name, partition_type, resources),
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

    if not await _record_hmc_step(
        steps,
        "network",
        hmc.add_network_adapter(created_uuid, network.port_vlan_id),
    ):
        _skip_steps(steps, step_names[2:])
        return _provision_result(creation, created_uuid, steps, False)

    if not await _record_hmc_step(
        steps,
        "vscsi",
        hmc.add_vscsi_adapter(
            created_uuid, network.vios_partition_id, network.vios_slot
        ),
    ):
        _skip_steps(steps, step_names[3:])
        return _provision_result(creation, created_uuid, steps, False)

    if not await _record_hmc_step(
        steps,
        "storage",
        hmc.map_storage_to_lpar(
            storage.vios_uuid,
            storage.kind,
            storage.storage_name,
            created_uuid,
        ),
    ):
        _skip_steps(steps, step_names[4:])
        return _provision_result(creation, created_uuid, steps, False)

    if power_on:
        if not await _record_hmc_step(
            steps,
            "power_on",
            hmc.submit_job(
                f"/rest/api/uom/LogicalPartition/{created_uuid}/do/PowerOn",
                power_on_lpar_job(),
            ),
        ):
            return _provision_result(creation, created_uuid, steps, False)

    return _provision_result(creation, created_uuid, steps, True)
