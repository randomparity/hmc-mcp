"""Presentation-neutral LPAR provisioning workflow.

Composes create_logical_partition + add_network_adapter + add_vscsi_adapter +
map_storage_to_lpar + power-on into a single call with a structured per-step
result and an optional dry-run that validates preconditions only.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .client import HMCClient
from .common import resolve_system_uuid
from .documents import LparResources, PartitionType, StorageKind, build_lpar_document
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


@dataclass
class _ProvisionState:
    """Validated output from the create step for later ordered steps."""

    creation: LparCreationResult | None = None
    created_uuid: str | None = None

    def require_created_uuid(self) -> str:
        if self.created_uuid is None:
            raise RuntimeError("LPAR UUID is unavailable before the create step")
        return self.created_uuid


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
    malformed_error: ValueError | None = None
    for net in networks:
        res = net.get("Resource") or {}
        vlan = res.get("NetworkVLANID")
        if vlan is None:
            continue
        try:
            parsed_vlan = int(vlan)
        except (TypeError, ValueError):
            identity = res.get("NetworkName") or net.get("UUID") or "unknown network"
            malformed_error = ValueError(
                f"VirtualNetwork {identity!r} has malformed NetworkVLANID {vlan!r}"
            )
            continue
        if parsed_vlan == port_vlan_id:
            return
    if malformed_error is not None:
        raise malformed_error
    raise ValueError(
        f"No VirtualNetwork with VLAN ID {port_vlan_id} found on system "
        f"{system_uuid!r}. Use hmc_list_virtual_networks to list available VLANs."
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


async def _run_steps(
    operations: Sequence[tuple[str, Callable[[], Awaitable[Any]]]],
) -> tuple[bool, list[dict[str, Any]]]:
    """Run operations in order and skip every step after the first failure."""
    steps: list[dict[str, Any]] = []
    failed = False
    for name, operation in operations:
        if failed:
            steps.append(_step(name, "skipped"))
            continue
        try:
            steps.append(_step(name, "ok", await operation()))
        except Exception as exc:
            steps.append(_step(name, "error", str(exc)))
            failed = True
    return not failed, steps


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
) -> dict[str, Any]:
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
    dict with:
    - ``created`` (bool): ``True`` when all required steps completed.
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
        return {
            "created": False,
            "dry_run": True,
            "ownership_stamped": None,
            "steps": [_step(n, "dry_run") for n in step_names],
            "warnings": [],
        }

    # ----------------------------------------------------------------
    # 4. Build LPAR XML
    # ----------------------------------------------------------------
    lpar_xml = build_lpar_document(
        name=name,
        partition_type=partition_type,
        resources=resources,
    )

    state = _ProvisionState()

    async def create() -> Any:
        creation_result = await create_and_stamp_lpar(
            hmc,
            system_uuid,
            system_name_or_uuid,
            LparCreation(name, partition_type, resources),
            lpar_xml,
        )
        created_lpar = creation_result["lpar"]
        uuid = (created_lpar or {}).get("UUID")
        if not isinstance(uuid, str) or not uuid:
            raise ValueError("LPAR creation returned no UUID")
        state.creation = creation_result
        state.created_uuid = uuid
        return created_lpar

    async def attach_network() -> Any:
        return await hmc.add_network_adapter(
            state.require_created_uuid(), network.port_vlan_id
        )

    async def vscsi() -> Any:
        return await hmc.add_vscsi_adapter(
            state.require_created_uuid(), network.vios_partition_id, network.vios_slot
        )

    async def map_storage() -> Any:
        return await hmc.map_storage_to_lpar(
            storage.vios_uuid,
            storage.kind,
            storage.storage_name,
            state.require_created_uuid(),
        )

    async def start() -> Any:
        uuid = state.require_created_uuid()
        return await hmc.submit_job(
            f"/rest/api/uom/LogicalPartition/{uuid}/do/PowerOn",
            power_on_lpar_job(),
        )

    operations = [
        ("create", create),
        ("network", attach_network),
        ("vscsi", vscsi),
        ("storage", map_storage),
    ]
    if power_on:
        operations.append(("power_on", start))
    created, steps = await _run_steps(operations)

    creation_result = state.creation
    return {
        "created": created,
        "dry_run": False,
        "ownership_stamped": (
            creation_result["ownership_stamped"] if creation_result else None
        ),
        "steps": steps,
        "warnings": creation_result["warnings"] if creation_result else [],
    }
