"""Presentation-neutral fleet health exception reporting."""

from __future__ import annotations

import asyncio
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from hmc_mcp.client.core import HMCClient
from ..errors import HMCError
from ..jobs import FAILED_JOB_STATUSES, job_outcome
from . import jobs as operations_jobs


_SYSTEM_WORKERS = 8
_MAX_SYSTEMS = 256
_MAX_RESOURCES_PER_SYSTEM = 10_000
_MAX_EXCEPTIONS = 10_000
_MAX_SCALAR_LENGTH = 500
_MAX_JOB_PARAMETERS = 10_000
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


def _sorted_records(records: list[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    return tuple(sorted(records, key=lambda record: (record["name"], record["uuid"])))


def _check_exception_budget(*categories: Collection[object]) -> None:
    if sum(map(len, categories)) > _MAX_EXCEPTIONS:
        raise ValueError(
            f"Fleet health result exceeds the safe limit of {_MAX_EXCEPTIONS} exceptions"
        )


def _check_job_parameter_budget(resource: dict[str, Any]) -> None:
    results = resource.get("Results")
    if not isinstance(results, dict):
        return
    parameters = results.get("JobParameter")
    if isinstance(parameters, list) and len(parameters) > _MAX_JOB_PARAMETERS:
        raise ValueError(
            "Fleet health job parameters exceed the safe limit of "
            f"{_MAX_JOB_PARAMETERS} entries"
        )


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
    _check_job_parameter_budget(resource)
    status = _text(resource.get("Status")).upper()
    if status not in FAILED_JOB_STATUSES:
        return None
    uuid = _text(job.get("UUID"))
    normalized_job = {**job, "Resource": {**resource, "Status": status}}
    error = job_outcome(uuid, normalized_job).error
    bounded_error = (
        error.strip()[:_MAX_ERROR_LENGTH]
        if isinstance(error, str) and error.strip()
        else "unknown"
    )
    return {
        "uuid": uuid,
        "name": _text(resource.get("JobName")),
        "status": status,
        "error": bounded_error,
    }


async def _recent_failed_jobs(
    hmc: HMCClient,
) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...]]:
    try:
        jobs = await operations_jobs.list_jobs(hmc)
    except HMCError as exc:
        if not operations_jobs.is_unsupported_job_listing(exc):
            raise
        return (), (_UNSUPPORTED_JOB_WARNING,)
    failures = [
        failure
        for job in jobs[:_RECENT_JOB_LIMIT]
        if (failure := _failed_job(job)) is not None
    ]
    return _sorted_records(failures), ()


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
        system_uuid = _text(uuid_value)
        system_name = _text(_resource(system).get("SystemName"))
        queue.put_nowait((system, system_uuid, system_name))
        exception = _system_exception(system)
        if exception is not None:
            system_exceptions.append(exception)
            _check_exception_budget(system_exceptions)

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
                _check_exception_budget(
                    system_exceptions, vios_exceptions, lpar_exceptions
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
    _check_exception_budget(
        system_exceptions, vios_exceptions, lpar_exceptions, failed_jobs
    )
    return FleetHealthResult(
        _sorted_records(system_exceptions),
        _sorted_records(vios_exceptions),
        _sorted_records(lpar_exceptions),
        failed_jobs,
        warnings,
    )
