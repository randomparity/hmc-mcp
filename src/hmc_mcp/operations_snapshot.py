"""Presentation-neutral portable LPAR snapshot capture."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from hmc_mcp.client import HMCClient
from hmc_mcp.common import resolve_lpar_uuid, resolve_system_name, resolve_system_uuid
from hmc_mcp.config import HMCConfig
from hmc_mcp.operations_ssh_network import (
    get_lpar_memopt_score,
    get_system_memopt_score,
    list_resource_group_memopt_scores,
    plan_lpar_memopt_scores,
    plan_resource_group_memopt_scores,
    plan_system_memopt_score,
)
from hmc_mcp.snapshot import (
    PLACEMENT_MEDIA_TYPE,
    PROFILE_MEDIA_TYPE,
    SCORES_MEDIA_TYPE,
    HmcIdentity,
    LparIdentity,
    LparSnapshot,
    NativeProfile,
    NormalizedConfiguration,
    ObservationEnvelope,
    SnapshotCapability,
    SnapshotConfiguration,
    SnapshotObservations,
    SnapshotInspection,
    SnapshotSource,
    SystemIdentity,
    inspect_snapshot,
    parse_snapshot,
    _normalized_from_profile,
    _parse_profile,
)
from hmc_mcp.ssh_commands import read_lpar_profile_record


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


async def validate_lpar_snapshot(document: str) -> dict[str, object]:
    """Validate local snapshot JSON through the supported async API contract."""
    snapshot = parse_snapshot(document)
    return {"valid": True, "format": snapshot.format, "version": snapshot.version}


async def inspect_lpar_snapshot(document: str) -> SnapshotInspection:
    """Inspect local snapshot identity through the supported async API contract."""
    return inspect_snapshot(document)


def _resource(entry: dict[str, Any] | None, label: str) -> dict[str, Any]:
    if entry is None or not isinstance(entry.get("Resource"), dict):
        raise ValueError(f"Snapshot capture cannot read {label} resource metadata")
    return entry["Resource"]


def _text(value: Any, label: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Snapshot capture requires nonblank {label}")
    return value


def _positive_int(value: Any, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Snapshot capture requires integer {label}") from exc
    if result <= 0:
        raise ValueError(f"Snapshot capture requires positive {label}")
    return result


def _placement(resource: dict[str, Any]) -> dict[str, object]:
    mode = "dedicated" if resource.get("HasDedicatedProcessors") is True else "shared"
    memory = resource.get("CurrentMemory")
    units = resource.get("CurrentProcessingUnits") if mode == "shared" else None
    dedicated = resource.get("DedicatedProcessors") if mode == "dedicated" else None
    return {
        "state": _text(resource.get("PartitionState"), "LPAR state"),
        "rmc_state": _text(resource.get("ResourceMonitoringControlState"), "RMC state", optional=True),
        "processor_mode": mode,
        "current_memory_mib": _positive_int(memory, "current memory") if memory is not None else None,
        "current_processor_units": float(units) if units is not None else None,
        "dedicated_processors": _positive_int(dedicated, "dedicated processors") if dedicated is not None else None,
    }


async def capture_lpar_snapshot(
    hmc: HMCClient,
    config: HMCConfig,
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    profile_name: str,
) -> LparSnapshot:
    """Capture one validated portable version-1 LPAR snapshot."""
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    system_name = await resolve_system_name(hmc, system_name_or_uuid)
    lpar_uuid = await resolve_lpar_uuid(
        hmc, lpar_name_or_uuid, system_name_or_uuid=system_uuid
    )
    console = await hmc.get_console_info()
    system = await hmc.get_managed_system(system_uuid)
    lpar = await hmc.get_logical_partition(lpar_uuid)
    console_resource = _resource(console, "HMC")
    system_resource = _resource(system, "managed-system")
    lpar_resource = _resource(lpar, "LPAR")
    lpar_name = _text(lpar_resource.get("PartitionName"), "LPAR name")
    assert isinstance(lpar_name, str)
    native_data = await read_lpar_profile_record(
        config, system_name, lpar_name, profile_name
    )
    normalized: NormalizedConfiguration = _normalized_from_profile(
        _parse_profile(native_data)
    )
    observed_at = _utcnow()
    current_lpar = await get_lpar_memopt_score(config, system_name, lpar_name)
    current_system = await get_system_memopt_score(config, system_name)
    predicted_lpars = await plan_lpar_memopt_scores(config, system_name)
    predicted_system = await plan_system_memopt_score(config, system_name)
    current_groups = await list_resource_group_memopt_scores(config, system_name)
    predicted_groups = await plan_resource_group_memopt_scores(config, system_name)
    mtms = _text(
        system_resource.get("MachineTypeModelSerialNumber"), "system MTMS"
    )
    assert isinstance(mtms, str)
    if "*" not in mtms:
        raise ValueError("Snapshot capture requires system MTMS in type-model*serial form")
    machine_type_model, serial = mtms.split("*", 1)
    hmc_uuid = _text(console.get("UUID") if console else None, "HMC UUID")
    system_id = _text(system.get("UUID") if system else None, "system UUID")
    lpar_id = _text(lpar.get("UUID") if lpar else None, "LPAR UUID")
    assert isinstance(hmc_uuid, str) and isinstance(system_id, str) and isinstance(lpar_id, str)
    snapshot = LparSnapshot(
        format="hmc-mcp.lpar-snapshot",
        version=1,
        captured_at=_utcnow(),
        source=SnapshotSource(
            hmc=HmcIdentity(
                uuid=hmc_uuid,
                name=_text(console_resource.get("HostName"), "HMC name", optional=True),
                version=_text(console_resource.get("Version"), "HMC version", optional=True),
            ),
            system=SystemIdentity(
                uuid=system_id,
                name=_text(system_resource.get("SystemName"), "system name", optional=True),
                machine_type_model=machine_type_model,
                serial=serial,
            ),
            lpar=LparIdentity(
                uuid=lpar_id,
                name=lpar_name,
                partition_id=_positive_int(lpar_resource.get("PartitionID"), "partition ID"),
            ),
        ),
        capabilities=(
            SnapshotCapability(name="affinity-scores", version=1, supported=True, collection="hmc-cli"),
            SnapshotCapability(name="lpar-profile-record", version=1, supported=True, collection="hmc-cli"),
            SnapshotCapability(name="runtime-placement", version=1, supported=True, collection="hmc-rest"),
        ),
        configuration=SnapshotConfiguration(
            profile_name=profile_name,
            native=NativeProfile(media_type=PROFILE_MEDIA_TYPE, data=native_data),
            normalized=normalized,
        ),
        observations=SnapshotObservations(
            observed_at=observed_at,
            runtime_placement=ObservationEnvelope(media_type=PLACEMENT_MEDIA_TYPE, data=_placement(lpar_resource)),
            scores=ObservationEnvelope(
                media_type=SCORES_MEDIA_TYPE,
                data={
                    "current": {"lpar": current_lpar, "system": current_system},
                    "predicted": {"lpars": predicted_lpars, "system": predicted_system},
                    "resource_groups": {
                        "current": asdict(current_groups),
                        "predicted": asdict(predicted_groups),
                    },
                },
            ),
        ),
    )
    return snapshot
