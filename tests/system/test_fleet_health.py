"""Tests for the presentation-neutral fleet health operation."""

from __future__ import annotations

import asyncio
from collections import Counter
from unittest.mock import AsyncMock

import pytest

from hmc_mcp.errors import HMCError
from hmc_mcp.jobs import FAILED_JOB_STATUSES
from hmc_mcp import operations_health
from hmc_mcp.operations_health import FleetHealthResult, fleet_health


def _entry(uuid: object, **resource: object) -> dict:
    return {"UUID": uuid, "Resource": resource}


def _healthy_client() -> AsyncMock:
    client = AsyncMock()
    client.list_managed_systems.return_value = [
        _entry("sys-1", SystemName="system-a", State="operating")
    ]
    client.list_logical_partitions.return_value = [
        _entry(
            "lpar-1",
            PartitionName="aix-a",
            PartitionState="running",
            ResourceMonitoringControlState="active",
        )
    ]
    client.list_vios.return_value = [
        _entry("vios-1", PartitionName="vios-a", PartitionState="running")
    ]
    client.list_uom.return_value = []
    return client


@pytest.mark.asyncio
async def test_healthy_estate_returns_empty_collections() -> None:
    result = await fleet_health(_healthy_client())

    assert result == FleetHealthResult((), (), (), (), ())


@pytest.mark.asyncio
async def test_degraded_estate_returns_curated_sorted_exceptions() -> None:
    client = _healthy_client()
    client.list_managed_systems.return_value = [
        _entry("sys-b", SystemName="system-b", State="standby"),
        _entry("sys-a", SystemName="system-a", State="OPERATING"),
    ]

    async def lpars(system_uuid: str) -> list[dict]:
        if system_uuid == "sys-b":
            return [
                _entry(
                    "lpar-z",
                    PartitionName="zeta",
                    PartitionState="running",
                    ResourceMonitoringControlState="inactive",
                )
            ]
        return []

    async def vios(system_uuid: str) -> list[dict]:
        if system_uuid == "sys-b":
            return [_entry("vios-z", PartitionName="zeta-vios", PartitionState="down")]
        return []

    client.list_logical_partitions.side_effect = lpars
    client.list_vios.side_effect = vios
    client.list_uom.return_value = [
        _entry(
            "job-1",
            JobName="failed-job",
            Status="failed_to_start",
            ResponseException={"Message": "could not start"},
        )
    ]

    result = await fleet_health(client)

    assert result.systems == (
        {"uuid": "sys-b", "name": "system-b", "state": "standby"},
    )
    assert result.vios == (
        {
            "uuid": "vios-z",
            "name": "zeta-vios",
            "state": "down",
            "system_uuid": "sys-b",
            "system_name": "system-b",
        },
    )
    assert result.lpars == (
        {
            "uuid": "lpar-z",
            "name": "zeta",
            "state": "running",
            "rmc_state": "inactive",
            "system_uuid": "sys-b",
            "system_name": "system-b",
        },
    )
    assert result.failed_jobs == (
        {
            "uuid": "job-1",
            "name": "failed-job",
            "status": "FAILED_TO_START",
            "error": "could not start",
        },
    )
    assert result.warnings == ()


@pytest.mark.asyncio
async def test_all_canonical_failed_job_statuses_are_reported() -> None:
    client = _healthy_client()
    client.list_uom.return_value = [
        _entry(f"job-{status}", JobName=status, Status=status)
        for status in sorted(FAILED_JOB_STATUSES)
    ] + [
        _entry("job-ok", JobName="ok", Status="COMPLETED_OK"),
        _entry("job-running", JobName="running", Status="RUNNING"),
        _entry("job-warning", JobName="warning", Status="COMPLETED_WITH_WARNINGS"),
        _entry("job-unknown", JobName="unknown", Status="mystery"),
    ]

    result = await fleet_health(client)

    assert {job["status"] for job in result.failed_jobs} == FAILED_JOB_STATUSES
    assert all(job["error"] == "unknown" for job in result.failed_jobs)


@pytest.mark.asyncio
async def test_job_filter_uses_first_twenty_feed_records_and_bounds_error() -> None:
    client = _healthy_client()
    client.list_uom.return_value = [
        _entry(f"ok-{index}", JobName=f"ok-{index}", Status="COMPLETED_OK")
        for index in range(20)
    ] + [_entry("late-failure", JobName="late", Status="FAILED")]
    assert (await fleet_health(client)).failed_jobs == ()

    client.list_uom.return_value = [
        _entry(
            "failed",
            JobName=None,
            Status="EXCEPTION",
            ResponseException={"Message": "x" * 600},
        )
    ]
    failed = (await fleet_health(client)).failed_jobs[0]
    assert failed["name"] == "unknown"
    assert failed["error"] == "x" * 500


@pytest.mark.asyncio
async def test_malformed_child_identities_remain_visible_as_unknown() -> None:
    client = _healthy_client()
    client.list_logical_partitions.return_value = [
        _entry(
            None,
            PartitionName=7,
            PartitionState=None,
            ResourceMonitoringControlState=None,
        )
    ]
    client.list_vios.return_value = [_entry(None, PartitionName=7, PartitionState=None)]
    client.list_uom.return_value = [_entry(None, JobName=7, Status="FAILED")]

    result = await fleet_health(client)

    assert result.lpars[0]["uuid"] == result.lpars[0]["name"] == "unknown"
    assert result.vios[0]["uuid"] == result.vios[0]["name"] == "unknown"
    assert result.failed_jobs[0]["uuid"] == "unknown"
    assert result.failed_jobs[0]["name"] == "unknown"


@pytest.mark.asyncio
async def test_core_inventory_error_propagates_without_partial_result() -> None:
    client = _healthy_client()
    error = HMCError("LPAR inventory failed", 500, "failure")
    client.list_logical_partitions.side_effect = error

    with pytest.raises(HMCError) as exc_info:
        await fleet_health(client)

    assert exc_info.value is error


@pytest.mark.asyncio
async def test_core_inventory_error_cancels_sibling_reads() -> None:
    client = _healthy_client()
    client.list_managed_systems.return_value = [
        _entry("sys-fail", SystemName="failing", State="operating"),
        _entry("sys-block", SystemName="blocked", State="operating"),
    ]
    sibling_started = asyncio.Event()
    sibling_cancelled = asyncio.Event()
    error = HMCError("LPAR inventory failed", 500, "failure")

    async def lpars(system_uuid: str) -> list[dict]:
        if system_uuid == "sys-fail":
            await sibling_started.wait()
            raise error
        sibling_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            sibling_cancelled.set()
            raise
        return []

    client.list_logical_partitions.side_effect = lpars

    with pytest.raises(HMCError) as exc_info:
        await fleet_health(client)

    assert exc_info.value is error
    assert sibling_cancelled.is_set()


@pytest.mark.asyncio
async def test_core_inventory_error_cancels_same_system_sibling_read() -> None:
    client = _healthy_client()
    sibling_started = asyncio.Event()
    sibling_cancelled = asyncio.Event()
    error = HMCError("LPAR inventory failed", 500, "failure")

    async def lpars(_system_uuid: str) -> list[dict]:
        await sibling_started.wait()
        raise error

    async def vios(_system_uuid: str) -> list[dict]:
        sibling_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            sibling_cancelled.set()
            raise
        return []

    client.list_logical_partitions.side_effect = lpars
    client.list_vios.side_effect = vios

    with pytest.raises(HMCError) as exc_info:
        await fleet_health(client)

    assert exc_info.value is error
    assert sibling_cancelled.is_set()


@pytest.mark.asyncio
async def test_unsupported_job_feed_preserves_shape_with_warning() -> None:
    client = _healthy_client()
    client.list_uom.side_effect = HMCError(
        "unsupported",
        400,
        "REST000E: Unrecognized root REST type of Job",
    )

    result = await fleet_health(client)

    assert result.failed_jobs == ()
    assert result.warnings == (
        "Recent job health is unavailable because this HMC does not support global Job listing.",
    )


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (500, "REST000E: Unrecognized root REST type of Job"),
        (400, "Unrecognized root REST type of Job"),
        (400, "REST000E: another error"),
    ],
)
@pytest.mark.asyncio
async def test_unexpected_job_errors_propagate(status: int, body: str) -> None:
    client = _healthy_client()
    error = HMCError("job failure", status, body)
    client.list_uom.side_effect = error

    with pytest.raises(HMCError) as exc_info:
        await fleet_health(client)

    assert exc_info.value is error


@pytest.mark.parametrize("uuid", [None, "", "  ", 42])
@pytest.mark.asyncio
async def test_malformed_system_identity_fails_before_child_reads(uuid: object) -> None:
    client = _healthy_client()
    client.list_managed_systems.return_value = [_entry(uuid, SystemName="broken")]

    with pytest.raises(ValueError, match="Managed system.*valid UUID"):
        await fleet_health(client)

    client.list_logical_partitions.assert_not_awaited()
    client.list_vios.assert_not_awaited()


@pytest.mark.asyncio
async def test_oversized_estate_fails_before_child_reads(monkeypatch) -> None:
    monkeypatch.setattr(operations_health, "_MAX_SYSTEMS", 1)
    client = _healthy_client()
    client.list_managed_systems.return_value *= 2

    with pytest.raises(ValueError, match="safe limit of 1 managed systems"):
        await fleet_health(client)

    client.list_logical_partitions.assert_not_awaited()
    client.list_vios.assert_not_awaited()


@pytest.mark.asyncio
async def test_oversized_system_inventory_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(operations_health, "_MAX_RESOURCES_PER_SYSTEM", 1)
    client = _healthy_client()
    client.list_logical_partitions.return_value *= 2

    with pytest.raises(ValueError, match="safe limit of 1 resources per category"):
        await fleet_health(client)


@pytest.mark.asyncio
async def test_system_workers_and_active_inspections_are_bounded(monkeypatch) -> None:
    active = 0
    maximum = 0
    release = asyncio.Event()
    calls: list[str] = []
    scheduled_coroutines: list[str] = []
    create_task = asyncio.create_task

    def recording_create_task(coro):
        scheduled_coroutines.append(coro.cr_code.co_name)
        return create_task(coro)

    monkeypatch.setattr(operations_health.asyncio, "create_task", recording_create_task)

    class RecordingClient:
        async def list_managed_systems(self) -> list[dict]:
            calls.append("list_managed_systems")
            return [
                _entry(f"sys-{index}", SystemName=f"system-{index}", State="operating")
                for index in range(30)
            ]

        async def list_logical_partitions(self, _uuid: str) -> list[dict]:
            nonlocal active, maximum
            calls.append("list_logical_partitions")
            active += 1
            maximum = max(maximum, active)
            if maximum == 8:
                release.set()
            await release.wait()
            active -= 1
            return []

        async def list_vios(self, _uuid: str) -> list[dict]:
            calls.append("list_vios")
            await release.wait()
            return []

        async def list_uom(self, resource_type: str) -> list[dict]:
            calls.append("list_uom")
            assert resource_type == "Job"
            return []

    result = await fleet_health(RecordingClient())  # type: ignore[arg-type]

    assert result == FleetHealthResult((), (), (), (), ())
    assert maximum == 8
    assert scheduled_coroutines.count("inspect_systems") == 8
    assert Counter(calls) == Counter(
        {
            "list_managed_systems": 1,
            "list_logical_partitions": 30,
            "list_vios": 30,
            "list_uom": 1,
        }
    )
