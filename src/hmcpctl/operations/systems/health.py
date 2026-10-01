"""Presentation-neutral fleet health issue reporting."""

from __future__ import annotations

import asyncio
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from hmcpctl.client.core import HMCClient

from ...errors import HMCError
from ...jobs import FAILED_JOB_STATUSES, job_identifier, job_outcome
from .. import jobs as operations_jobs

_SYSTEM_WORKERS = 8
_MAX_SYSTEMS = 256
_MAX_RESOURCES_PER_SYSTEM = 10_000
_MAX_ISSUES = 10_000
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
    state = _bounded_text_or_unknown(resource.get("PartitionState")).lower()
    rmc_state = _bounded_text_or_unknown(
        resource.get("ResourceMonitoringControlState")
    ).lower()
    # A partition that is not activated has no RMC connection to report.
    if state == "not activated" or rmc_state in {"active", "busy"}:
        return None
    return {
        "uuid": _bounded_text_or_unknown(lpar.get("UUID")),
        "name": _bounded_text_or_unknown(resource.get("PartitionName")),
        "state": state,
        "rmc_state": rmc_state,
        "system_uuid": system_uuid,
        "system_name": system_name,
    }


def _operation_name(resource: dict[str, Any]) -> object:
    """Return ``JobRequestInstance/RequestedOperation/OperationName``.

    A JobResponse has no ``JobName``; it names the operation in its request
    instance (`PowerOn` in the captured V10R3 job reads, #1202).
    """
    request = resource.get("JobRequestInstance")
    operation = request.get("RequestedOperation") if isinstance(request, dict) else None
    return operation.get("OperationName") if isinstance(operation, dict) else None


def _failed_job(job: dict[str, Any]) -> dict[str, Any] | None:
    resource = _resource(job)
    _check_job_parameter_budget(resource)
    status = _bounded_text_or_unknown(resource.get("Status")).upper()
    if status not in FAILED_JOB_STATUSES:
        return None
    job_id = _bounded_text_or_unknown(job_identifier(job))
    normalized_job = {**job, "Resource": {**resource, "Status": status}}
    error = job_outcome(job_id, normalized_job).error
    bounded_error = (
        error.strip()[:_MAX_ERROR_LENGTH]
        if isinstance(error, str) and error.strip()
        else "unknown"
    )
    return {
        "job_id": job_id,
        "name": _bounded_text_or_unknown(_operation_name(resource)),
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
    return _sorted_records(failures, "job_id"), ()


async def _vios_or_warning(
    hmc: HMCClient, system_uuid: str, system_name: str
) -> tuple[list[dict[str, Any]], str | None]:
    """Return a system's VIOS entries, or none and a warning on HMC refusal.

    A V11R2 HMC answers a system's VIOS feed with HTTP 500 when a VIOS cannot
    report its storage (#1202); the rest of the estate is still reported. The
    partition feed stays core inventory: its failure fails the whole result.
    """
    try:
        return await hmc.list_vios(system_uuid), None
    except HMCError as exc:
        warning = f"VIOS inventory for system {system_name} is unavailable: {exc}"
        return [], warning[:_MAX_ERROR_LENGTH]


async def _system_inventory(
    hmc: HMCClient, system_uuid: str, system_name: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    lpar_task = asyncio.create_task(hmc.list_logical_partitions(system_uuid))
    vios_task = asyncio.create_task(_vios_or_warning(hmc, system_uuid, system_name))
    tasks = (lpar_task, vios_task)
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    lpars = lpar_task.result()
    vioses, vios_warning = vios_task.result()
    if (
        len(lpars) > _MAX_RESOURCES_PER_SYSTEM
        or len(vioses) > _MAX_RESOURCES_PER_SYSTEM
    ):
        raise ValueError(
            f"Fleet health inventory for system {system_uuid} exceeds the safe "
            f"limit of {_MAX_RESOURCES_PER_SYSTEM} resources per category"
        )
    return lpars, vioses, vios_warning


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
    inventory_warnings: list[str] = []

    async def inspect_systems() -> None:
        while not queue.empty():
            try:
                _, system_uuid, system_name = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                lpars, vioses, vios_warning = await _system_inventory(
                    hmc, system_uuid, system_name
                )
                if vios_warning is not None:
                    inventory_warnings.append(vios_warning)
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
    job_task = asyncio.create_task(_recent_failed_jobs(hmc))
    tasks = (*worker_tasks, job_task)
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    failed_jobs, job_warnings = job_task.result()
    _check_issue_budget(system_issues, vios_issues, lpar_issues, failed_jobs)
    return FleetHealthResult(
        _sorted_records(system_issues),
        _sorted_records(vios_issues),
        _sorted_records(lpar_issues),
        failed_jobs,
        (*sorted(inventory_warnings), *job_warnings),
    )
