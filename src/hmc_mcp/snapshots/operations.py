"""Presentation-neutral portable LPAR snapshot capture."""

from __future__ import annotations

import asyncio
import math
import re
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, Literal, overload

from hmc_mcp.client.core import HMCClient
from hmc_mcp.operations.affinity import (
    AffinityAssessmentInput,
    AffinityAssessmentResult,
    PolicyState,
    assess_affinity,
)
from hmc_mcp.operations.affinity.ssh import (
    get_lpar_memopt_score,
    get_minimum_affinity_policy,
    get_system_memopt_score,
    list_resource_group_memopt_scores,
    plan_lpar_memopt_scores,
    plan_resource_group_memopt_scores,
    plan_system_memopt_score,
)
from hmc_mcp.resource_identity import (
    resolve_lpar_uuid,
    resolve_system_name,
    resolve_system_uuid,
)
from hmc_mcp.ssh.profiles import read_lpar_profile_record

from .models import (
    MINIMUM_AFFINITY_POLICY_MEDIA_TYPE,
    PLACEMENT_MEDIA_TYPE,
    PROFILE_MEDIA_TYPE,
    SCORES_MEDIA_TYPE,
    HMCIdentity,
    LparIdentity,
    LparSnapshot,
    NativeProfile,
    NormalizedConfiguration,
    ObservationEnvelope,
    SnapshotCapability,
    SnapshotConfiguration,
    SnapshotInspection,
    SnapshotObservations,
    SnapshotSource,
    SystemIdentity,
    _normalized_from_profile,
    _parse_profile,
    inspect_snapshot,
    parse_snapshot,
    serialize_snapshot,
)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _captured_lpar_score(snapshot: LparSnapshot) -> int | None:
    """Select the captured LPAR score that matches the snapshot identity."""
    scores = snapshot.observations.scores
    if scores is None:
        return None
    current = scores.data.get("current")
    if not isinstance(current, dict):
        return None
    rows = current.get("lpar")
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        return None
    identity = snapshot.source.lpar
    matches: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        has_name = "lpar_name" in row
        has_id = "lpar_id" in row
        name_matches = not has_name or row.get("lpar_name") == identity.name
        id_matches = not has_id or str(row.get("lpar_id")) == str(identity.partition_id)
        if name_matches and id_matches and (has_name or has_id):
            matches.append(row)
    if len(matches) != 1:
        return None
    raw = matches[0].get("curr_lpar_score")
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        return None
    if isinstance(raw, str) and re.fullmatch(r"(?:0|[1-9][0-9]{0,2})", raw) is None:
        return None
    score = int(raw)
    return score if 0 <= score <= 100 else None


async def assess_snapshot_affinity(
    document: str,
    *,
    current_score: int | None,
    predicted_score: int | None,
    policy_state: PolicyState = "absent",
    configured_minimum: int | None = None,
    regression_threshold: int | None = None,
    optimization_threshold: int | None = None,
    stale_after_seconds: int = 86400,
    assessed_at: datetime | None = None,
) -> AffinityAssessmentResult:
    """Assess explicit current evidence without blocking the caller's event loop."""
    return await asyncio.to_thread(
        _assess_snapshot_affinity,
        document,
        current_score=current_score,
        predicted_score=predicted_score,
        policy_state=policy_state,
        configured_minimum=configured_minimum,
        regression_threshold=regression_threshold,
        optimization_threshold=optimization_threshold,
        stale_after_seconds=stale_after_seconds,
        assessed_at=assessed_at,
    )


def _assess_snapshot_affinity(
    document: str,
    *,
    current_score: int | None,
    predicted_score: int | None,
    policy_state: PolicyState,
    configured_minimum: int | None,
    regression_threshold: int | None,
    optimization_threshold: int | None,
    stale_after_seconds: int,
    assessed_at: datetime | None,
) -> AffinityAssessmentResult:
    snapshot = parse_snapshot(document)
    captured_policy = snapshot.observations.minimum_affinity_policy
    policy_capability = next(
        (
            capability
            for capability in snapshot.capabilities
            if capability.name == "minimum-affinity-policy"
        ),
        None,
    )
    if policy_capability is None:
        captured_policy_state = "missing"
    elif not policy_capability.supported:
        captured_policy_state = "unsupported"
    elif captured_policy is None:
        captured_policy_state = "absent"
    else:
        captured_policy_state = "configured"
    captured_minimum = (
        captured_policy.data.get("min_affinity_score")
        if captured_policy is not None
        else None
    )
    return assess_affinity(
        AffinityAssessmentInput(
            captured_score=_captured_lpar_score(snapshot),
            current_score=current_score,
            predicted_score=predicted_score,
            policy_state=policy_state,
            captured_policy_state=captured_policy_state,
            configured_minimum=configured_minimum,
            captured_minimum=captured_minimum,
            captured_at=snapshot.captured_at,
            assessed_at=assessed_at or _utcnow(),
            stale_after_seconds=stale_after_seconds,
            regression_threshold=regression_threshold,
            optimization_threshold=optimization_threshold,
        )
    )


async def validate_lpar_snapshot(document: str) -> dict[str, object]:
    """Validate local snapshot JSON without blocking the caller's event loop."""
    snapshot = await asyncio.to_thread(parse_snapshot, document)
    return {"valid": True, "format": snapshot.format, "version": snapshot.version}


async def inspect_lpar_snapshot(document: str) -> SnapshotInspection:
    """Inspect local snapshot identity without blocking the caller's event loop."""
    return await asyncio.to_thread(inspect_snapshot, document)


def _resource(entry: dict[str, Any] | None, label: str) -> dict[str, Any]:
    if entry is None or not isinstance(entry.get("Resource"), dict):
        raise ValueError(f"Snapshot capture cannot read {label} resource metadata")
    return entry["Resource"]


@overload
def _text(value: Any, label: str, *, optional: Literal[False] = False) -> str: ...


@overload
def _text(value: Any, label: str, *, optional: Literal[True]) -> str | None: ...


def _text(value: Any, label: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Snapshot capture requires nonblank {label}")
    return value


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"Snapshot capture requires integer {label}")  # noqa: TRY004 - ValueError is the ADR 0029 exported contract, asserted in tests/unit/test_snapshot_capture.py
    if isinstance(value, str) and not value.isdecimal():
        raise ValueError(f"Snapshot capture requires integer {label}")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Snapshot capture requires integer {label}") from exc
    if result <= 0:
        raise ValueError(f"Snapshot capture requires positive {label}")
    return result


def _runtime_int(value: Any, label: str) -> int | None:
    if isinstance(value, bool):
        raise ValueError(f"Snapshot capture requires integer {label}")  # noqa: TRY004 - ValueError is the ADR 0029 exported contract, asserted in tests/unit/test_snapshot_capture.py
    if value in (None, 0, "0"):
        return None
    return _positive_int(value, label)


def _runtime_float(value: Any, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"Snapshot capture requires numeric {label}")  # noqa: TRY004 - ValueError is the ADR 0029 exported contract, asserted in tests/unit/test_snapshot_capture.py
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Snapshot capture requires numeric {label}") from exc
    if result == 0:
        return None
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"Snapshot capture requires positive finite {label} or zero")
    return result


def _placement(resource: dict[str, Any]) -> dict[str, object]:
    dedicated_value = resource.get("HasDedicatedProcessors")
    if dedicated_value in (True, "true"):
        mode = "dedicated"
    elif dedicated_value in (False, "false"):
        mode = "shared"
    else:
        raise ValueError("Snapshot capture requires true/false HasDedicatedProcessors")
    memory = resource.get("CurrentMemory")
    units = resource.get("CurrentProcessingUnits") if mode == "shared" else None
    dedicated = resource.get("DedicatedProcessors") if mode == "dedicated" else None
    return {
        "state": _text(resource.get("PartitionState"), "LPAR state"),
        "rmc_state": _text(
            resource.get("ResourceMonitoringControlState"), "RMC state", optional=True
        ),
        "processor_mode": mode,
        "current_memory_mib": _runtime_int(memory, "current memory"),
        "current_processor_units": _runtime_float(units, "current processor units"),
        "dedicated_processors": _runtime_int(dedicated, "dedicated processors"),
    }


async def capture_lpar_snapshot(
    hmc: HMCClient,
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
    native_data = await read_lpar_profile_record(
        hmc.config, system_name, lpar_name, profile_name
    )
    normalized: NormalizedConfiguration = _normalized_from_profile(
        _parse_profile(native_data)
    )
    observed_at = _utcnow()
    current_lpar = await get_lpar_memopt_score(hmc, system_name, lpar_name)
    current_system = await get_system_memopt_score(hmc, system_name)
    predicted_lpars = await plan_lpar_memopt_scores(hmc, system_name)
    predicted_system = await plan_system_memopt_score(hmc, system_name)
    current_groups = await list_resource_group_memopt_scores(hmc, system_name)
    predicted_groups = await plan_resource_group_memopt_scores(hmc, system_name)
    minimum_policy = await get_minimum_affinity_policy(
        hmc, system_name, lpar_name
    )
    mtms = system_resource.get("MachineTypeModelSerialNumber")
    if isinstance(mtms, dict):
        machine_type = _text(mtms.get("MachineType"), "system machine type")
        model = _text(mtms.get("Model"), "system model")
        serial = _text(mtms.get("SerialNumber"), "system serial")
        machine_type_model = f"{machine_type}-{model}"
    else:
        mtms_text = _text(mtms, "system MTMS")
        if "*" not in mtms_text:
            raise ValueError(
                "Snapshot capture requires system MTMS in type-model*serial form"
            )
        machine_type_model, serial = mtms_text.split("*", 1)
    hmc_uuid = _text(console.get("UUID") if console else None, "HMC UUID")
    system_id = _text(system.get("UUID") if system else None, "system UUID")
    lpar_id = _text(lpar.get("UUID") if lpar else None, "LPAR UUID")
    snapshot = LparSnapshot(
        format="hmc-mcp.lpar-snapshot",
        version=1,
        captured_at=_utcnow(),
        source=SnapshotSource(
            hmc=HMCIdentity(
                uuid=hmc_uuid,
                name=_text(console_resource.get("HostName"), "HMC name", optional=True),
                version=_text(
                    console_resource.get("Version"), "HMC version", optional=True
                ),
            ),
            system=SystemIdentity(
                uuid=system_id,
                name=_text(
                    system_resource.get("SystemName"), "system name", optional=True
                ),
                machine_type_model=machine_type_model,
                serial=serial,
            ),
            lpar=LparIdentity(
                uuid=lpar_id,
                name=lpar_name,
                partition_id=_positive_int(
                    lpar_resource.get("PartitionID"), "partition ID"
                ),
            ),
        ),
        capabilities=(
            SnapshotCapability(
                name="affinity-scores", version=1, supported=True, collection="hmc-cli"
            ),
            SnapshotCapability(
                name="lpar-profile-record",
                version=1,
                supported=True,
                collection="hmc-cli",
            ),
            SnapshotCapability(
                name="minimum-affinity-policy",
                version=1,
                supported=minimum_policy.capability == "available",
                collection="hmc-cli",
                unavailable_reason=minimum_policy.unavailable_reason,
            ),
            SnapshotCapability(
                name="runtime-placement",
                version=1,
                supported=True,
                collection="hmc-rest",
            ),
        ),
        configuration=SnapshotConfiguration(
            profile_name=profile_name,
            native=NativeProfile(media_type=PROFILE_MEDIA_TYPE, data=native_data),
            normalized=normalized,
        ),
        observations=SnapshotObservations(
            observed_at=observed_at,
            runtime_placement=ObservationEnvelope(
                media_type=PLACEMENT_MEDIA_TYPE, data=_placement(lpar_resource)
            ),
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
            minimum_affinity_policy=(
                ObservationEnvelope(
                    media_type=MINIMUM_AFFINITY_POLICY_MEDIA_TYPE,
                    data={
                        "min_affinity_score": minimum_policy.min_affinity_score,
                        "min_affinity_score_action": (
                            minimum_policy.min_affinity_score_action
                        ),
                    },
                )
                if minimum_policy.capability == "available"
                else None
            ),
        ),
    )
    serialize_snapshot(snapshot)
    return snapshot
