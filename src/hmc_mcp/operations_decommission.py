"""Presentation-neutral LPAR decommission orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from .client_adapters import AdapterType
from .client import HMCClient
from .common import is_uuid, resolve_system_uuid
from .errors import HMCError
from .jobs import (
    SUCCESSFUL_JOB_STATUSES,
    job_identifier,
    job_outcome,
    power_off_lpar_job,
    validate_wait_timing,
    wait_for_submitted_job,
)
from .operations_lpar import (
    authorize_lpar_mutation,
    read_lpar_ownership_owner,
    resolve_lpar_ownership_names,
)

_ADAPTER_ORDER: tuple[AdapterType, ...] = (
    "ClientNetworkAdapter",
    "VirtualSCSIClientAdapter",
    "VirtualFibreChannelClientAdapter",
    "VirtualNICDedicated",
)
_STORAGE_MAPPING_TYPES: tuple[tuple[str, str], ...] = (
    ("VirtualSCSIMappings", "VirtualSCSIMapping"),
    ("VirtualFibreChannelMappings", "VirtualFibreChannelMapping"),
)


@dataclass(frozen=True)
class DecommissionResult:
    """Truthful outcome of an LPAR decommission request."""

    resource_deleted: bool = field(
        metadata={"description": "Whether the final LPAR delete completed."}
    )
    workflow_completed: bool = field(
        metadata={"description": "Whether every requested workflow step completed."}
    )
    lpar_uuid: str = field(
        metadata={"description": "UUID of the resolved target LPAR."}
    )
    dry_run: bool = field(
        metadata={"description": "Whether the call only inventoried the blast radius."}
    )
    steps: tuple[dict[str, Any], ...] = field(
        metadata={"description": "Ordered per-step status and curated result records."}
    )
    warnings: tuple[str, ...] = field(
        metadata={"description": "Non-fatal warnings discovered during inventory."}
    )
    blast_radius: dict[str, Any] = field(
        metadata={
            "description": "Curated inventory of the LPAR, adapters, and observed storage mappings."
        }
    )


@dataclass(frozen=True)
class _Inventory:
    lpar_uuid: str
    lpar_name: str
    partition_id: int | None
    state: str | None
    owner: str | None
    adapters: tuple[dict[str, str], ...]
    storage_mappings: tuple[dict[str, str], ...]
    unresolved_storage_mapping_count: int
    warnings: tuple[str, ...]

    def blast_radius(self) -> dict[str, Any]:
        return {
            "lpar_uuid": self.lpar_uuid,
            "lpar_name": self.lpar_name,
            "partition_id": self.partition_id,
            "state": self.state,
            "owner": self.owner,
            "adapters": self.adapters,
            "storage_mappings": self.storage_mappings,
            "unresolved_storage_mapping_count": self.unresolved_storage_mapping_count,
        }


def _step(name: str, status: str, result: Any = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"step": name, "status": status}
    if result is not None:
        entry["result"] = result
    return entry


def _skip_steps(steps: list[dict[str, Any]], *names: str) -> None:
    steps.extend(_step(name, "skipped") for name in names)


def _resource(entry: dict[str, Any] | None) -> dict[str, Any]:
    value = (entry or {}).get("Resource")
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _as_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _records(container: Any, singular: str) -> tuple[dict[str, Any], ...]:
    if isinstance(container, list):
        return tuple(item for item in container if isinstance(item, dict))
    if isinstance(container, dict):
        if singular in container:
            return _records(container[singular], singular)
        return (container,)
    return ()


def _extract_target_uuid(link: Any) -> str | None:
    if isinstance(link, dict):
        href = link.get("href") or link.get("link")
        return _extract_target_uuid(href)
    if not isinstance(link, str) or "/LogicalPartition/" not in link:
        return None
    return link.rsplit("/LogicalPartition/", 1)[-1].strip("/") or None


def _mapping_matches_target(
    mapping: dict[str, Any], lpar_uuid: str, partition_id: str | None
) -> bool | None:
    link_uuid = _extract_target_uuid(mapping.get("AssociatedLogicalPartition"))
    if link_uuid is not None:
        return link_uuid == lpar_uuid

    candidate_ids = (
        _text(mapping.get("AssociatedLogicalPartitionID")),
        _text(mapping.get("ClientPartitionID")),
        _text(mapping.get("RemoteLogicalPartitionID")),
        _text((_resource({"Resource": mapping.get("ClientAdapter")}) or {}).get("RemoteLogicalPartitionID")),
        _text((_resource({"Resource": mapping.get("ServerAdapter")}) or {}).get("RemoteLogicalPartitionID")),
        _text(mapping.get("ConnectingPartitionID")),
    )
    for candidate in candidate_ids:
        if candidate is not None and partition_id is not None:
            return candidate == partition_id
    return None


def _backing_device(mapping: dict[str, Any]) -> str | None:
    storage = mapping.get("Storage")
    if isinstance(storage, dict):
        physical = storage.get("PhysicalVolume")
        if isinstance(physical, dict):
            name = _text(physical.get("VolumeName"))
            if name is not None:
                return name
        virtual_disk = storage.get("VirtualDisk")
        if isinstance(virtual_disk, dict):
            name = _text(virtual_disk.get("DiskName"))
            if name is not None:
                return name
        logical_unit = storage.get("LogicalUnit")
        if isinstance(logical_unit, dict):
            name = _text(logical_unit.get("UnitName"))
            if name is not None:
                return name
    port = mapping.get("Port")
    if isinstance(port, dict):
        return _text(port.get("WWPNPair"))
    return None


async def _resolve_target_lpar(
    hmc: HMCClient, system_uuid: str, lpar_name_or_uuid: str
) -> dict[str, Any]:
    children = await hmc.list_logical_partitions(system_uuid)
    if is_uuid(lpar_name_or_uuid):
        matches = [
            child
            for child in children
            if _text(child.get("UUID")) == lpar_name_or_uuid
        ]
        if len(matches) == 1:
            return matches[0]
        raise ValueError(
            f"No LPAR {lpar_name_or_uuid!r} found on managed system {system_uuid!r}."
        )

    matches = [
        child
        for child in children
        if _text(_resource(child).get("PartitionName")) == lpar_name_or_uuid
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(
            f"No LPAR named {lpar_name_or_uuid!r} found on managed system {system_uuid!r}."
        )
    identities = ", ".join(
        str(child.get("UUID") or "unknown") for child in sorted(matches, key=lambda item: str(item.get("UUID")))
    )
    raise ValueError(
        f"Ambiguous LPAR name {lpar_name_or_uuid!r} on managed system {system_uuid!r}: {identities}"
    )


async def _inventory(hmc: HMCClient, system_name_or_uuid: str, lpar_name_or_uuid: str, *, ownership_override: bool) -> _Inventory:
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    child = await _resolve_target_lpar(hmc, system_uuid, lpar_name_or_uuid)
    lpar_uuid = _text(child.get("UUID"))
    if lpar_uuid is None:
        raise ValueError("Resolved LPAR child is missing its UUID.")

    system_name, lpar_name = await resolve_lpar_ownership_names(
        hmc, system_uuid, system_name_or_uuid, lpar_uuid
    )
    await authorize_lpar_mutation(
        hmc, system_name, lpar_name, ownership_override=ownership_override
    )
    owner = await read_lpar_ownership_owner(hmc, system_name, lpar_name)

    lpar = await hmc.get_logical_partition(lpar_uuid)
    resource = _resource(lpar)
    partition_name = _text(resource.get("PartitionName")) or lpar_name
    partition_id = _as_int(resource.get("PartitionID"))
    state = _text(resource.get("PartitionState"))

    adapters: list[dict[str, str]] = []
    for adapter_type in _ADAPTER_ORDER:
        entries = await hmc.list_adapters(lpar_uuid, adapter_type)
        for entry in sorted(entries, key=lambda item: str(item.get("UUID") or "")):
            uuid = _text(entry.get("UUID"))
            if uuid is None:
                continue
            adapters.append({"type": adapter_type, "uuid": uuid})

    storage_mappings: list[dict[str, str]] = []
    unresolved = 0
    partition_id_text = str(partition_id) if partition_id is not None else None
    for vios in await hmc.list_vios(system_uuid):
        vios_uuid = _text(vios.get("UUID"))
        if vios_uuid is None:
            continue
        detail = await hmc.get_vios_storage_detail(vios_uuid)
        detail_resource = _resource(detail)
        for block_name, item_name in _STORAGE_MAPPING_TYPES:
            for mapping in _records(detail_resource.get(block_name), item_name):
                matches = _mapping_matches_target(mapping, lpar_uuid, partition_id_text)
                if matches is True:
                    record = {
                        "vios_uuid": vios_uuid,
                        "type": item_name,
                        "uuid": _text(mapping.get("UUID")) or "unknown",
                    }
                    backing_device = _backing_device(mapping)
                    if backing_device is not None:
                        record["backing_device"] = backing_device
                    storage_mappings.append(record)
                elif matches is None:
                    unresolved += 1

    warnings: list[str] = []
    if unresolved:
        warnings.append(
            "Storage blast radius may be incomplete: "
            f"{unresolved} mapping(s) lacked enough client identity to prove they belong "
            f"to LPAR {partition_name!r}."
        )

    return _Inventory(
        lpar_uuid=lpar_uuid,
        lpar_name=partition_name,
        partition_id=partition_id,
        state=state,
        owner=owner,
        adapters=tuple(adapters),
        storage_mappings=tuple(storage_mappings),
        unresolved_storage_mapping_count=unresolved,
        warnings=tuple(warnings),
    )


async def _power_off(
    hmc: HMCClient,
    inventory: _Inventory,
    *,
    immediate: bool,
    timeout_seconds: int,
    poll_interval: int,
) -> dict[str, Any]:
    if inventory.state == "not activated":
        return _step(
            "power_off",
            "ok",
            {"already_off": True, "state": "not activated"},
        )
    submitted_job = await hmc.submit_job(
        f"/rest/api/uom/LogicalPartition/{inventory.lpar_uuid}/do/PowerOff",
        power_off_lpar_job(immediate=immediate),
    )
    completed_job = await wait_for_submitted_job(
        hmc, submitted_job, True, timeout_seconds, poll_interval
    )
    submitted_id = job_identifier(submitted_job) if submitted_job is not None else None
    outcome = job_outcome(submitted_id or "", completed_job)
    if outcome.timed_out:
        return _step(
            "power_off",
            "error",
            f"PowerOff for LPAR {inventory.lpar_name!r} timed out before reaching a successful terminal status.",
        )
    if outcome.status not in SUCCESSFUL_JOB_STATUSES:
        detail = outcome.error or "no error detail returned"
        return _step(
            "power_off",
            "error",
            f"PowerOff for LPAR {inventory.lpar_name!r} did not complete successfully "
            f"(status {outcome.status!r}: {detail}).",
        )
    return _step(
        "power_off",
        "ok",
        {
            "already_off": False,
            "job_id": outcome.job_id,
            "status": outcome.status,
        },
    )


async def _detach_adapters(hmc: HMCClient, inventory: _Inventory) -> dict[str, Any]:
    deleted: list[dict[str, str]] = []
    for adapter in inventory.adapters:
        try:
            await hmc.delete_adapter(
                inventory.lpar_uuid,
                cast(AdapterType, adapter["type"]),
                adapter["uuid"],
            )
        except HMCError as exc:
            return _step(
                "detach_adapters",
                "error",
                {
                    "adapters": tuple(deleted),
                    "error": str(exc),
                },
            )
        deleted.append(adapter)
    return _step(
        "detach_adapters",
        "ok",
        {"adapters": inventory.adapters},
    )


async def decommission_lpar(
    hmc: HMCClient,
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    *,
    dry_run: bool = False,
    ownership_override: bool = False,
    immediate: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
) -> DecommissionResult:
    """Inventory, authorize, and optionally decommission one LPAR."""
    validate_wait_timing(True, timeout_seconds, poll_interval)
    inventory = await _inventory(
        hmc,
        system_name_or_uuid,
        lpar_name_or_uuid,
        ownership_override=ownership_override,
    )

    if dry_run:
        return DecommissionResult(
            resource_deleted=False,
            workflow_completed=True,
            lpar_uuid=inventory.lpar_uuid,
            dry_run=True,
            steps=(
                _step("power_off", "dry_run", {"state": inventory.state}),
                _step(
                    "detach_adapters",
                    "dry_run",
                    {"adapters": inventory.adapters},
                ),
                _step("delete_lpar", "dry_run", {"lpar_uuid": inventory.lpar_uuid}),
            ),
            warnings=inventory.warnings,
            blast_radius=inventory.blast_radius(),
        )

    steps: list[dict[str, Any]] = []

    try:
        power_step = await _power_off(
            hmc,
            inventory,
            immediate=immediate,
            timeout_seconds=timeout_seconds,
            poll_interval=poll_interval,
        )
    except HMCError as exc:
        steps.append(_step("power_off", "error", str(exc)))
        _skip_steps(steps, "detach_adapters", "delete_lpar")
        return DecommissionResult(
            resource_deleted=False,
            workflow_completed=False,
            lpar_uuid=inventory.lpar_uuid,
            dry_run=False,
            steps=tuple(steps),
            warnings=inventory.warnings,
            blast_radius=inventory.blast_radius(),
        )
    steps.append(power_step)
    if power_step["status"] != "ok":
        _skip_steps(steps, "detach_adapters", "delete_lpar")
        return DecommissionResult(
            resource_deleted=False,
            workflow_completed=False,
            lpar_uuid=inventory.lpar_uuid,
            dry_run=False,
            steps=tuple(steps),
            warnings=inventory.warnings,
            blast_radius=inventory.blast_radius(),
        )

    detach_step = await _detach_adapters(hmc, inventory)
    steps.append(detach_step)
    if detach_step["status"] != "ok":
        steps.append(_step("delete_lpar", "skipped"))
        return DecommissionResult(
            resource_deleted=False,
            workflow_completed=False,
            lpar_uuid=inventory.lpar_uuid,
            dry_run=False,
            steps=tuple(steps),
            warnings=inventory.warnings,
            blast_radius=inventory.blast_radius(),
        )

    try:
        await hmc.delete_logical_partition(inventory.lpar_uuid)
    except HMCError as exc:
        steps.append(_step("delete_lpar", "error", str(exc)))
        return DecommissionResult(
            resource_deleted=False,
            workflow_completed=False,
            lpar_uuid=inventory.lpar_uuid,
            dry_run=False,
            steps=tuple(steps),
            warnings=inventory.warnings,
            blast_radius=inventory.blast_radius(),
        )

    steps.append(_step("delete_lpar", "ok", {"lpar_uuid": inventory.lpar_uuid}))
    return DecommissionResult(
        resource_deleted=True,
        workflow_completed=True,
        lpar_uuid=inventory.lpar_uuid,
        dry_run=False,
        steps=tuple(steps),
        warnings=inventory.warnings,
        blast_radius=inventory.blast_radius(),
    )
