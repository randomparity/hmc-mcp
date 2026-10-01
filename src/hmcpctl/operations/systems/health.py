"""Presentation-neutral fleet health issue reporting."""

from __future__ import annotations

import asyncio
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from hmcpctl.client.core import HMCClient

_SYSTEM_WORKERS = 8
_MAX_SYSTEMS = 256
_MAX_RESOURCES_PER_SYSTEM = 10_000
_MAX_ISSUES = 10_000
_MAX_SCALAR_LENGTH = 500


@dataclass(frozen=True)
class FleetHealthResult:
    """Curated unhealthy resources across one HMC-managed estate."""

    systems: tuple[dict[str, Any], ...]
    vios: tuple[dict[str, Any], ...]
    lpars: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]


def _bounded_text_or_unknown(value: object) -> str:
    if isinstance(value, str) and value.strip():
        normalized = value.strip()
        if len(normalized) > _MAX_SCALAR_LENGTH:
            raise ValueError(
                f"Fleet health scalar exceeds the safe limit of {_MAX_SCALAR_LENGTH} characters"
            )
        return normalized
    return "unknown"


def _resource(entry: dict[str, Any]) -> dict[str, Any]:
    value = entry.get("Resource")
    return value if isinstance(value, dict) else {}


def _sorted_records(
    records: list[dict[str, Any]], id_key: str = "uuid"
) -> tuple[dict[str, Any], ...]:
    return tuple(sorted(records, key=lambda record: (record["name"], record[id_key])))


def _check_issue_budget(*categories: Collection[object]) -> None:
    if sum(map(len, categories)) > _MAX_ISSUES:
        raise ValueError(
            f"Fleet health result exceeds the safe limit of {_MAX_ISSUES} issues"
        )


def _system_issue(system: dict[str, Any]) -> dict[str, Any] | None:
    resource = _resource(system)
    state = _bounded_text_or_unknown(resource.get("State")).lower()
    if state == "operating":
        return None
    return {
        "uuid": _bounded_text_or_unknown(system.get("UUID")),
        "name": _bounded_text_or_unknown(resource.get("SystemName")),
        "state": state,
    }


def _vios_issue(
    vios: dict[str, Any], system_uuid: str, system_name: str
) -> dict[str, Any] | None:
    resource = _resource(vios)
    state = _bounded_text_or_unknown(resource.get("PartitionState")).lower()
    if state == "running":
        return None
    return {
        "uuid": _bounded_text_or_unknown(vios.get("UUID")),
        "name": _bounded_text_or_unknown(resource.get("PartitionName")),
        "state": state,
        "system_uuid": system_uuid,
        "system_name": system_name,
    }


def _lpar_issue(
    lpar: dict[str, Any], system_uuid: str, system_name: str
) -> dict[str, Any] | None:
    resource = _resource(lpar)
    rmc_state = _bounded_text_or_unknown(
        resource.get("ResourceMonitoringControlState") or resource.get("RMCState")
    ).lower()
    if rmc_state in {"active", "busy"}:
        return None
    return {
        "uuid": _bounded_text_or_unknown(lpar.get("UUID")),
        "name": _bounded_text_or_unknown(resource.get("PartitionName")),
        "state": _bounded_text_or_unknown(resource.get("PartitionState")).lower(),
        "rmc_state": rmc_state,
        "system_uuid": system_uuid,
        "system_name": system_name,
    }


async def _system_inventory(
    hmc: HMCClient, system_uuid: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    lpar_task = asyncio.create_task(hmc.list_logical_partitions(system_uuid))
    vios_task = asyncio.create_task(hmc.list_vios(system_uuid))
    tasks = (lpar_task, vios_task)
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    lpars = lpar_task.result()
    vioses = vios_task.result()
    if (
        len(lpars) > _MAX_RESOURCES_PER_SYSTEM
        or len(vioses) > _MAX_RESOURCES_PER_SYSTEM
    ):
        raise ValueError(
            f"Fleet health inventory for system {system_uuid} exceeds the safe "
            f"limit of {_MAX_RESOURCES_PER_SYSTEM} resources per category"
        )
    return lpars, vioses


async def fetch_fleet_health(hmc: HMCClient) -> FleetHealthResult:
    """Return curated unhealthy resources from the configured HMC estate."""
    systems = await hmc.list_managed_systems()
    if len(systems) > _MAX_SYSTEMS:
        raise ValueError(
            f"Fleet health inventory exceeds the safe limit of {_MAX_SYSTEMS} "
            "managed systems"
        )
    queue: asyncio.Queue[tuple[dict[str, Any], str, str]] = asyncio.Queue()
    system_issues: list[dict[str, Any]] = []
    for system in systems:
        uuid_value = system.get("UUID")
        if not isinstance(uuid_value, str) or not uuid_value.strip():
            raise ValueError("Managed system entry must contain a valid UUID")
        system_uuid = _bounded_text_or_unknown(uuid_value)
        system_name = _bounded_text_or_unknown(_resource(system).get("SystemName"))
        queue.put_nowait((system, system_uuid, system_name))
        issue = _system_issue(system)
        if issue is not None:
            system_issues.append(issue)
            _check_issue_budget(system_issues)

    vios_issues: list[dict[str, Any]] = []
    lpar_issues: list[dict[str, Any]] = []

    async def inspect_systems() -> None:
        while not queue.empty():
            try:
                _, system_uuid, system_name = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                lpars, vioses = await _system_inventory(hmc, system_uuid)
                lpar_issues.extend(
                    issue
                    for lpar in lpars
                    if (issue := _lpar_issue(lpar, system_uuid, system_name))
                    is not None
                )
                vios_issues.extend(
                    issue
                    for vios in vioses
                    if (issue := _vios_issue(vios, system_uuid, system_name))
                    is not None
                )
                _check_issue_budget(system_issues, vios_issues, lpar_issues)
            finally:
                queue.task_done()

    worker_count = min(_SYSTEM_WORKERS, len(systems))
    worker_tasks = [asyncio.create_task(inspect_systems()) for _ in range(worker_count)]
    try:
        await asyncio.gather(*worker_tasks)
    except BaseException:
        for task in worker_tasks:
            task.cancel()
        await asyncio.gather(*worker_tasks, return_exceptions=True)
        raise
    return FleetHealthResult(
        _sorted_records(system_issues),
        _sorted_records(vios_issues),
        _sorted_records(lpar_issues),
        (),
    )
