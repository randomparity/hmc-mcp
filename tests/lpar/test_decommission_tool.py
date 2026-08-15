"""Tests for the presentation-neutral LPAR decommission workflow and MCP tool."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from hmc_mcp.errors import HMCError
from hmc_mcp.operations_decommission import DecommissionResult, decommission_lpar
from hmc_mcp.server import hmc_decommission_lpar

SYSTEM_UUID = "11111111-1111-1111-1111-111111111111"
LPAR_UUID = "22222222-2222-2222-2222-222222222222"
VIOS_UUID = "33333333-3333-3333-3333-333333333333"
TARGET_PARTITION_ID = "7"


def _lpar(
    uuid: str = LPAR_UUID,
    *,
    name: str = "aix-prod",
    partition_id: str = TARGET_PARTITION_ID,
    state: str = "running",
) -> dict[str, object]:
    return {
        "UUID": uuid,
        "Resource": {
            "PartitionName": name,
            "PartitionID": partition_id,
            "PartitionState": state,
        },
    }


def _adapter(adapter_type: str, uuid: str) -> dict[str, object]:
    return {"UUID": uuid, "AdapterType": adapter_type}


def _storage_detail() -> dict[str, object]:
    return {
        "UUID": VIOS_UUID,
        "Resource": {
            "VirtualSCSIMappings": {
                "VirtualSCSIMapping": [
                    {
                        "UUID": "vscsi-match",
                        "AssociatedLogicalPartition": {
                            "href": f"/rest/api/uom/LogicalPartition/{LPAR_UUID}"
                        },
                        "Storage": {"PhysicalVolume": {"VolumeName": "hdisk10"}},
                    },
                    {
                        "UUID": "vscsi-sparse",
                        "Storage": {"LogicalUnit": {"UnitName": "lu01"}},
                    },
                    {
                        "UUID": "vscsi-other",
                        "AssociatedLogicalPartition": {
                            "href": "/rest/api/uom/LogicalPartition/not-target"
                        },
                        "Storage": {"PhysicalVolume": {"VolumeName": "hdisk99"}},
                    },
                ]
            },
            "VirtualFibreChannelMappings": {
                "VirtualFibreChannelMapping": [
                    {
                        "UUID": "vfc-match",
                        "AssociatedLogicalPartitionID": TARGET_PARTITION_ID,
                        "Port": {"WWPNPair": "C050760AAA C050760BBB"},
                    }
                ]
            },
        },
    }


def _client() -> AsyncMock:
    hmc = AsyncMock()
    hmc.list_logical_partitions.return_value = [_lpar()]
    hmc.get_logical_partition.return_value = _lpar()

    async def list_adapters(_lpar_uuid: str, adapter_type: str) -> list[dict[str, object]]:
        adapters = {
            "ClientNetworkAdapter": [
                _adapter("ClientNetworkAdapter", "cna-2"),
                _adapter("ClientNetworkAdapter", "cna-1"),
            ],
            "VirtualSCSIClientAdapter": [
                _adapter("VirtualSCSIClientAdapter", "vscsi-2"),
                _adapter("VirtualSCSIClientAdapter", "vscsi-1"),
            ],
            "VirtualFibreChannelClientAdapter": [
                _adapter("VirtualFibreChannelClientAdapter", "vfc-2")
            ],
            "VirtualNICDedicated": [_adapter("VirtualNICDedicated", "vnic-2")],
        }
        return adapters[adapter_type]

    hmc.list_adapters.side_effect = list_adapters
    hmc.list_vios.return_value = [{"UUID": VIOS_UUID, "Resource": {"PartitionName": "vios1"}}]
    hmc.get_vios_storage_detail.return_value = _storage_detail()
    hmc.wait_for_job.return_value = {
        "UUID": "job-uuid",
        "Resource": {"JobID": "job-uuid", "Status": "COMPLETED_OK"},
    }
    hmc.submit_job.return_value = {"UUID": "job-uuid", "link": "/rest/api/uom/jobs/job-uuid"}
    hmc.delete_storage_mapping = AsyncMock()
    hmc.delete_virtual_disk = AsyncMock()
    hmc.delete_logical_unit = AsyncMock()
    return hmc


def _patch_common(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    from hmc_mcp import operations_decommission as ops

    async def resolve_system_uuid(hmc, value: str) -> str:
        calls.append(f"resolve_system_uuid:{value}")
        assert hmc is not None
        return SYSTEM_UUID

    async def resolve_names(hmc, system_uuid: str, fallback: str, lpar_uuid: str) -> tuple[str, str]:
        calls.append(f"resolve_names:{system_uuid}:{fallback}:{lpar_uuid}")
        return ("system-a", "aix-prod")

    async def authorize(hmc, system_name: str, lpar_name: str, *, ownership_override: bool = False) -> None:
        calls.append(f"authorize:{system_name}:{lpar_name}:{ownership_override}")

    async def read_owner(hmc, system_name: str, lpar_name: str) -> None:
        calls.append(f"read_owner:{system_name}:{lpar_name}")
        return None

    monkeypatch.setattr(ops, "resolve_system_uuid", resolve_system_uuid)
    monkeypatch.setattr(ops, "resolve_lpar_ownership_names", resolve_names)
    monkeypatch.setattr(ops, "authorize_lpar_mutation", authorize)
    monkeypatch.setattr(ops, "read_lpar_ownership_owner", read_owner)


def _tool_result() -> DecommissionResult:
    return DecommissionResult(
        resource_deleted=True,
        workflow_completed=True,
        lpar_uuid=LPAR_UUID,
        dry_run=False,
        steps=(
            {
                "step": "power_off",
                "status": "ok",
                "result": {"already_off": True, "state": "not activated"},
            },
        ),
        warnings=(),
        blast_radius={
            "lpar_uuid": LPAR_UUID,
            "lpar_name": "aix-prod",
            "partition_id": 7,
            "state": "not activated",
            "owner": None,
            "adapters": (),
            "storage_mappings": (),
            "unresolved_storage_mapping_count": 0,
        },
    )


def test_hmc_decommission_lpar_delegates_with_one_configured_client() -> None:
    expected = _tool_result()
    entered_clients: list[tuple[str | None, object]] = []
    hmc = object()

    @asynccontextmanager
    async def fake_client_context(profile: str | None):
        entered_clients.append((profile, hmc))
        yield hmc

    def fake_client_from_env(profile: str | None = None):
        return fake_client_context(profile)

    with (
        patch("hmc_mcp.server_lpars.client_from_env", side_effect=fake_client_from_env),
        patch(
            "hmc_mcp.server_lpars.decommission_lpar",
            new=AsyncMock(return_value=expected),
        ) as decommission_mock,
    ):
        result = hmc_decommission_lpar(
            system_name_or_uuid="system-a",
            lpar_name_or_uuid="aix-prod",
            dry_run=True,
            ownership_override=True,
            immediate=True,
            timeout_seconds=123,
            poll_interval=7,
            profile="ops",
        )

    assert result == expected
    assert entered_clients == [("ops", hmc)]
    decommission_mock.assert_awaited_once_with(
        hmc,
        "system-a",
        "aix-prod",
        dry_run=True,
        ownership_override=True,
        immediate=True,
        timeout_seconds=123,
        poll_interval=7,
    )


@pytest.mark.asyncio
async def test_decommission_rejects_uuid_outside_selected_system(monkeypatch: pytest.MonkeyPatch) -> None:
    hmc = _client()
    hmc.list_logical_partitions.return_value = [_lpar(uuid="other-uuid")]
    authorize = AsyncMock()

    from hmc_mcp import operations_decommission as ops

    monkeypatch.setattr(ops, "resolve_system_uuid", AsyncMock(return_value=SYSTEM_UUID))
    monkeypatch.setattr(ops, "authorize_lpar_mutation", authorize)

    with pytest.raises(ValueError, match="No LPAR .* on managed system"):
        await decommission_lpar(hmc, "system-a", LPAR_UUID)

    hmc.list_logical_partitions.assert_awaited_once_with(SYSTEM_UUID)
    authorize.assert_not_awaited()
    hmc.delete_logical_partition.assert_not_awaited()


@pytest.mark.asyncio
async def test_decommission_resolves_uuid_case_insensitively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    hmc_uuid = "abcdefab-cdef-abcd-efab-cdefabcdefab"
    hmc = _client()
    hmc.list_logical_partitions.return_value = [_lpar(uuid=hmc_uuid)]
    hmc.get_logical_partition.return_value = _lpar(uuid=hmc_uuid)
    _patch_common(monkeypatch, calls)

    result = await decommission_lpar(
        hmc,
        "system-a",
        hmc_uuid.upper(),
        dry_run=True,
    )

    assert result.lpar_uuid == hmc_uuid
    hmc.get_logical_partition.assert_awaited_once_with(hmc_uuid)
    assert [call.args[0] for call in hmc.list_adapters.await_args_list] == [hmc_uuid] * 4


@pytest.mark.asyncio
async def test_decommission_refuses_incomplete_adapter_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    hmc = _client()
    _patch_common(monkeypatch, calls)

    async def list_adapters(
        _lpar_uuid: str, adapter_type: str
    ) -> list[dict[str, object]]:
        if adapter_type == "ClientNetworkAdapter":
            return [{"AdapterType": adapter_type}]
        return []

    hmc.list_adapters.side_effect = list_adapters

    with pytest.raises(
        ValueError,
        match=(
            "Cannot safely decommission LPAR .*ClientNetworkAdapter.*missing its UUID"
        ),
    ):
        await decommission_lpar(hmc, "system-a", "aix-prod")

    hmc.submit_job.assert_not_awaited()
    hmc.delete_adapter.assert_not_awaited()
    hmc.delete_logical_partition.assert_not_awaited()
    hmc.delete_storage_mapping.assert_not_awaited()
    hmc.delete_virtual_disk.assert_not_awaited()
    hmc.delete_logical_unit.assert_not_awaited()


@pytest.mark.asyncio
async def test_decommission_dry_run_inventories_without_mutating(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    hmc = _client()
    _patch_common(monkeypatch, calls)

    result = await decommission_lpar(hmc, "system-a", "aix-prod", dry_run=True)

    assert result == DecommissionResult(
        resource_deleted=False,
        workflow_completed=True,
        lpar_uuid=LPAR_UUID,
        dry_run=True,
        steps=(
            {"step": "power_off", "status": "dry_run", "result": {"state": "running"}},
            {
                "step": "detach_adapters",
                "status": "dry_run",
                "result": {
                    "adapters": (
                        {"type": "ClientNetworkAdapter", "uuid": "cna-1"},
                        {"type": "ClientNetworkAdapter", "uuid": "cna-2"},
                        {"type": "VirtualSCSIClientAdapter", "uuid": "vscsi-1"},
                        {"type": "VirtualSCSIClientAdapter", "uuid": "vscsi-2"},
                        {"type": "VirtualFibreChannelClientAdapter", "uuid": "vfc-2"},
                        {"type": "VirtualNICDedicated", "uuid": "vnic-2"},
                    )
                },
            },
            {
                "step": "delete_lpar",
                "status": "dry_run",
                "result": {"lpar_uuid": LPAR_UUID},
            },
        ),
        warnings=(
            "Storage blast radius may be incomplete: 1 mapping(s) lacked enough client identity to prove they belong to LPAR 'aix-prod'.",
        ),
        blast_radius={
            "lpar_uuid": LPAR_UUID,
            "lpar_name": "aix-prod",
            "partition_id": 7,
            "state": "running",
            "owner": None,
            "adapters": (
                {"type": "ClientNetworkAdapter", "uuid": "cna-1"},
                {"type": "ClientNetworkAdapter", "uuid": "cna-2"},
                {"type": "VirtualSCSIClientAdapter", "uuid": "vscsi-1"},
                {"type": "VirtualSCSIClientAdapter", "uuid": "vscsi-2"},
                {"type": "VirtualFibreChannelClientAdapter", "uuid": "vfc-2"},
                {"type": "VirtualNICDedicated", "uuid": "vnic-2"},
            ),
            "storage_mappings": (
                {
                    "vios_uuid": VIOS_UUID,
                    "type": "VirtualSCSIMapping",
                    "uuid": "vscsi-match",
                    "backing_device": "hdisk10",
                },
                {
                    "vios_uuid": VIOS_UUID,
                    "type": "VirtualFibreChannelMapping",
                    "uuid": "vfc-match",
                    "backing_device": "C050760AAA C050760BBB",
                },
            ),
            "unresolved_storage_mapping_count": 1,
        },
    )
    assert calls == [
        "resolve_system_uuid:system-a",
        f"resolve_names:{SYSTEM_UUID}:system-a:{LPAR_UUID}",
        "authorize:system-a:aix-prod:False",
        "read_owner:system-a:aix-prod",
    ]
    hmc.submit_job.assert_not_awaited()
    hmc.wait_for_job.assert_not_awaited()
    hmc.delete_adapter.assert_not_awaited()
    hmc.delete_logical_partition.assert_not_awaited()
    hmc.delete_storage_mapping.assert_not_awaited()
    hmc.delete_virtual_disk.assert_not_awaited()
    hmc.delete_logical_unit.assert_not_awaited()


@pytest.mark.asyncio
async def test_decommission_enforces_ownership_even_for_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    hmc = _client()

    from hmc_mcp import operations_decommission as ops

    monkeypatch.setattr(ops, "resolve_system_uuid", AsyncMock(return_value=SYSTEM_UUID))
    monkeypatch.setattr(
        ops,
        "resolve_lpar_ownership_names",
        AsyncMock(return_value=("system-a", "aix-prod")),
    )
    monkeypatch.setattr(
        ops,
        "authorize_lpar_mutation",
        AsyncMock(side_effect=PermissionError("foreign owner")),
    )
    monkeypatch.setattr(ops, "read_lpar_ownership_owner", AsyncMock(return_value=None))

    with pytest.raises(PermissionError, match="foreign owner"):
        await decommission_lpar(hmc, "system-a", "aix-prod", dry_run=True)

    hmc.get_quick_property.assert_not_awaited()
    hmc.submit_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_decommission_allows_explicit_ownership_override(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    hmc = _client()
    _patch_common(monkeypatch, calls)

    result = await decommission_lpar(
        hmc,
        "system-a",
        "aix-prod",
        dry_run=True,
        ownership_override=True,
    )

    assert result.workflow_completed is True
    assert "authorize:system-a:aix-prod:True" in calls


@pytest.mark.asyncio
async def test_decommission_runs_power_off_adapter_delete_and_lpar_delete_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    hmc = _client()
    _patch_common(monkeypatch, calls)
    hmc.submit_job.side_effect = lambda *args, **kwargs: calls.append("submit_job") or {
        "UUID": "job-uuid",
        "link": "/rest/api/uom/jobs/job-uuid",
    }
    hmc.wait_for_job.side_effect = lambda *args, **kwargs: calls.append("wait_for_job") or {
        "UUID": "job-uuid",
        "Resource": {"JobID": "job-uuid", "Status": "COMPLETED_OK"},
    }
    hmc.delete_adapter.side_effect = (
        lambda lpar_uuid, adapter_type, adapter_uuid: calls.append(
            f"delete_adapter:{adapter_type}:{adapter_uuid}"
        )
    )
    hmc.delete_logical_partition.side_effect = lambda uuid: calls.append(
        f"delete_lpar:{uuid}"
    )

    result = await decommission_lpar(
        hmc,
        "system-a",
        "aix-prod",
        immediate=True,
        timeout_seconds=90,
        poll_interval=3,
    )

    assert result.resource_deleted is True
    assert result.workflow_completed is True
    assert result.steps == (
        {
            "step": "power_off",
            "status": "ok",
            "result": {
                "already_off": False,
                "job_id": "job-uuid",
                "status": "COMPLETED_OK",
            },
        },
        {
            "step": "detach_adapters",
            "status": "ok",
            "result": {
                "adapters": (
                    {"type": "ClientNetworkAdapter", "uuid": "cna-1"},
                    {"type": "ClientNetworkAdapter", "uuid": "cna-2"},
                    {"type": "VirtualSCSIClientAdapter", "uuid": "vscsi-1"},
                    {"type": "VirtualSCSIClientAdapter", "uuid": "vscsi-2"},
                    {"type": "VirtualFibreChannelClientAdapter", "uuid": "vfc-2"},
                    {"type": "VirtualNICDedicated", "uuid": "vnic-2"},
                )
            },
        },
        {"step": "delete_lpar", "status": "ok", "result": {"lpar_uuid": LPAR_UUID}},
    )
    assert calls == [
        "resolve_system_uuid:system-a",
        f"resolve_names:{SYSTEM_UUID}:system-a:{LPAR_UUID}",
        "authorize:system-a:aix-prod:False",
        "read_owner:system-a:aix-prod",
        "submit_job",
        "wait_for_job",
        "delete_adapter:ClientNetworkAdapter:cna-1",
        "delete_adapter:ClientNetworkAdapter:cna-2",
        "delete_adapter:VirtualSCSIClientAdapter:vscsi-1",
        "delete_adapter:VirtualSCSIClientAdapter:vscsi-2",
        "delete_adapter:VirtualFibreChannelClientAdapter:vfc-2",
        "delete_adapter:VirtualNICDedicated:vnic-2",
        f"delete_lpar:{LPAR_UUID}",
    ]


@pytest.mark.asyncio
async def test_decommission_marks_already_off_lpar_without_power_job(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    hmc = _client()
    hmc.get_logical_partition.return_value = _lpar(state="not activated")
    _patch_common(monkeypatch, calls)

    result = await decommission_lpar(hmc, "system-a", "aix-prod")

    assert result.steps[0] == {
        "step": "power_off",
        "status": "ok",
        "result": {"already_off": True, "state": "not activated"},
    }
    hmc.submit_job.assert_not_awaited()
    hmc.wait_for_job.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("job", "fragment"),
    [
        (
            {"UUID": "job-uuid", "Resource": {"JobID": "job-uuid", "Status": "FAILED", "ResponseException": {"Message": "power failed"}}},
            "status 'FAILED'",
        ),
        (
            {"UUID": "job-uuid", "Resource": {"JobID": "job-uuid", "Status": "RUNNING"}},
            "timed out",
        ),
    ],
)
async def test_decommission_reports_power_off_failure_and_skips_later_steps(
    monkeypatch: pytest.MonkeyPatch,
    job: dict[str, object],
    fragment: str,
) -> None:
    calls: list[str] = []
    hmc = _client()
    _patch_common(monkeypatch, calls)
    hmc.wait_for_job.return_value = job

    result = await decommission_lpar(hmc, "system-a", "aix-prod")

    assert result.resource_deleted is False
    assert result.workflow_completed is False
    assert result.steps[0]["status"] == "error"
    assert fragment in result.steps[0]["result"]
    assert result.steps[1:] == (
        {"step": "detach_adapters", "status": "skipped"},
        {"step": "delete_lpar", "status": "skipped"},
    )
    hmc.delete_adapter.assert_not_awaited()
    hmc.delete_logical_partition.assert_not_awaited()


@pytest.mark.asyncio
async def test_decommission_stops_after_first_adapter_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    hmc = _client()
    _patch_common(monkeypatch, calls)

    async def delete_adapter(_lpar_uuid: str, adapter_type: str, adapter_uuid: str) -> None:
        calls.append(f"delete_adapter:{adapter_type}:{adapter_uuid}")
        if adapter_uuid == "vscsi-1":
            raise HMCError("adapter delete failed")

    hmc.delete_adapter.side_effect = delete_adapter

    result = await decommission_lpar(hmc, "system-a", "aix-prod")

    assert result.resource_deleted is False
    assert result.workflow_completed is False
    assert result.steps == (
        {
            "step": "power_off",
            "status": "ok",
            "result": {
                "already_off": False,
                "job_id": "job-uuid",
                "status": "COMPLETED_OK",
            },
        },
        {
            "step": "detach_adapters",
            "status": "error",
            "result": {
                "adapters": (
                    {"type": "ClientNetworkAdapter", "uuid": "cna-1"},
                    {"type": "ClientNetworkAdapter", "uuid": "cna-2"},
                ),
                "error": "adapter delete failed",
            },
        },
        {"step": "delete_lpar", "status": "skipped"},
    )
    assert calls == [
        "resolve_system_uuid:system-a",
        f"resolve_names:{SYSTEM_UUID}:system-a:{LPAR_UUID}",
        "authorize:system-a:aix-prod:False",
        "read_owner:system-a:aix-prod",
        "delete_adapter:ClientNetworkAdapter:cna-1",
        "delete_adapter:ClientNetworkAdapter:cna-2",
        "delete_adapter:VirtualSCSIClientAdapter:vscsi-1",
    ]
    hmc.delete_logical_partition.assert_not_awaited()
