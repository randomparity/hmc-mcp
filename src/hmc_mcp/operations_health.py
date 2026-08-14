"""Presentation-neutral fleet health exception reporting."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .client import HMCClient
from .errors import HMCError
from .jobs import FAILED_JOB_STATUSES, job_outcome


_SYSTEM_WORKERS = 8
_MAX_SYSTEMS = 256
_MAX_RESOURCES_PER_SYSTEM = 10_000
_RECENT_JOB_LIMIT = 20
_MAX_ERROR_LENGTH = 500
_UNSUPPORTED_JOB_WARNING = "Recent job health is unavailable because this HMC does not support global Job listing."


@dataclass(frozen=True)
class FleetHealthResult:
    """Curated unhealthy resources across one HMC-managed estate."""

    systems: tuple[dict[str, Any], ...]
    vios: tuple[dict[str, Any], ...]
    lpars: tuple[dict[str, Any], ...]
    failed_jobs: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]


def _text(value: object) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "unknown"


def _resource(entry: dict[str, Any]) -> dict[str, Any]:
    value = entry.get("Resource")
    return value if isinstance(value, dict) else {}


def _sort(records: list[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    records.sort(key=lambda record: (record["name"], record["uuid"]))
    return tuple(records)


def _system_exception(system: dict[str, Any]) -> dict[str, Any] | None:
    resource = _resource(system)
    state = _text(resource.get("State")).lower()
    if state == "operating":
        return None
    return {
        "uuid": _text(system.get("UUID")),
        "name": _text(resource.get("SystemName")),
        "state": state,
    }


def _vios_exception(
    vios: dict[str, Any], system_uuid: str, system_name: str
) -> dict[str, Any] | None:
    resource = _resource(vios)
    state = _text(resource.get("PartitionState")).lower()
    if state == "running":
        return None
    return {
        "uuid": _text(vios.get("UUID")),
        "name": _text(resource.get("PartitionName")),
        "state": state,
        "system_uuid": system_uuid,
        "system_name": system_name,
    }


def _lpar_exception(
    lpar: dict[str, Any], system_uuid: str, system_name: str
) -> dict[str, Any] | None:
    resource = _resource(lpar)
    rmc_state = _text(
        resource.get("ResourceMonitoringControlState") or resource.get("RMCState")
    ).lower()
    if rmc_state in {"active", "busy"}:
        return None
    return {
        "uuid": _text(lpar.get("UUID")),
        "name": _text(resource.get("PartitionName")),
        "state": _text(resource.get("PartitionState")).lower(),
        "rmc_state": rmc_state,
        "system_uuid": system_uuid,
        "system_name": system_name,
    }


def _failed_job(job: dict[str, Any]) -> dict[str, Any] | None:
    resource = _resource(job)
    status = _text(resource.get("Status")).upper()
    if status not in FAILED_JOB_STATUSES:
        return None
    uuid = _text(job.get("UUID"))
    normalized_job = {**job, "Resource": {**resource, "Status": status}}
    error = job_outcome(uuid, normalized_job).error
    bounded_error = _text(error)[:_MAX_ERROR_LENGTH]
    return {
        "uuid": uuid,
        "name": _text(resource.get("JobName")),
        "status": status,
        "error": bounded_error,
    }


def _unsupported_job_listing(exc: HMCError) -> bool:
    body = exc.body or ""
    return (
        exc.status_code == 400
        and "REST000E" in body
        and "Unrecognized root REST type of Job" in body
    )


async def _recent_failed_jobs(
    hmc: HMCClient,
) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...]]:
    try:
        jobs = await hmc.list_uom("Job")
    except HMCError as exc:
        if not _unsupported_job_listing(exc):
            raise
        return (), (_UNSUPPORTED_JOB_WARNING,)
    failures = [
        failure
        for job in jobs[:_RECENT_JOB_LIMIT]
        if (failure := _failed_job(job)) is not None
    ]
    return _sort(failures), ()


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


async def fleet_health(hmc: HMCClient) -> FleetHealthResult:
    """Return curated unhealthy resources from the configured HMC estate."""
    systems = await hmc.list_managed_systems()
    if len(systems) > _MAX_SYSTEMS:
        raise ValueError(
            f"Fleet health inventory exceeds the safe limit of {_MAX_SYSTEMS} "
            "managed systems"
        )
    queue: asyncio.Queue[tuple[dict[str, Any], str, str]] = asyncio.Queue()
    system_exceptions: list[dict[str, Any]] = []
    for system in systems:
        uuid_value = system.get("UUID")
        if not isinstance(uuid_value, str) or not uuid_value.strip():
            raise ValueError("Managed system entry must contain a valid UUID")
        system_uuid = uuid_value.strip()
        system_name = _text(_resource(system).get("SystemName"))
        queue.put_nowait((system, system_uuid, system_name))
        exception = _system_exception(system)
        if exception is not None:
            system_exceptions.append(exception)

    vios_exceptions: list[dict[str, Any]] = []
    lpar_exceptions: list[dict[str, Any]] = []

    async def inspect_systems() -> None:
        while not queue.empty():
            try:
                _, system_uuid, system_name = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                lpars, vioses = await _system_inventory(hmc, system_uuid)
                lpar_exceptions.extend(
                    exception
                    for lpar in lpars
                    if (exception := _lpar_exception(lpar, system_uuid, system_name))
                    is not None
                )
                vios_exceptions.extend(
                    exception
                    for vios in vioses
                    if (exception := _vios_exception(vios, system_uuid, system_name))
                    is not None
                )
            finally:
                queue.task_done()

    worker_count = min(_SYSTEM_WORKERS, len(systems))
    worker_tasks = [asyncio.create_task(inspect_systems()) for _ in range(worker_count)]
    job_task = asyncio.create_task(_recent_failed_jobs(hmc))
    tasks = (*worker_tasks, job_task)
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    failed_jobs, warnings = job_task.result()
    return FleetHealthResult(
        _sort(system_exceptions),
        _sort(vios_exceptions),
        _sort(lpar_exceptions),
        failed_jobs,
        warnings,
    )
