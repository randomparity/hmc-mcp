"""Tests for the presentation-neutral LPAR decommission workflow and MCP tool."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from conftest import assert_only_these_client_methods_used

from hmc_mcp.config import HMCConfig
from hmc_mcp.errors import HMCError
from hmc_mcp.operations.lpar.assignments import WorkflowStep
from hmc_mcp.operations.lpar.decommission import DecommissionResult, decommission_lpar
from hmc_mcp.server_tools.lpar.lifecycle import hmc_decommission_lpar as hmc_decommission_lpar

SYSTEM_UUID = "11111111-1111-1111-1111-111111111111"
LPAR_UUID = "22222222-2222-2222-2222-222222222222"
VIOS_UUID = "33333333-3333-3333-3333-333333333333"
TARGET_PARTITION_ID = "7"


def _workflow_steps(*entries: dict[str, Any]) -> tuple[WorkflowStep, ...]:
    return tuple(
        WorkflowStep(entry["step"], entry["status"], entry.get("result"))
        for entry in entries
    )


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
    hmc.get_quick_property.return_value = "not activated"

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
    from hmc_mcp.operations.lpar import decommission as ops

    async def resolve_system_uuid(hmc, value: str) -> str:
        calls.append(f"resolve_system_uuid:{value}")
        assert hmc is not None
        return SYSTEM_UUID

    async def resolve_names(hmc, system_uuid: str, fallback: str, lpar_uuid: str) -> tuple[str, str]:
        calls.append(f"resolve_names:{system_uuid}:{fallback}:{lpar_uuid}")
        return ("system-a", "aix-prod")

    async def authorize(hmc, system_name: str, lpar_name: str, *, ownership_override: bool = False) -> None:
        calls.append(f"authorize:{system_name}:{lpar_name}:{ownership_override}")

    monkeypatch.setattr(ops, "resolve_system_uuid", resolve_system_uuid)
    monkeypatch.setattr(ops, "resolve_lpar_ownership_names", resolve_names)
    monkeypatch.setattr(ops, "authorize_decommission_lpar_ownership_snapshot", authorize)


def _tool_result() -> DecommissionResult:
    return DecommissionResult(
        resource_deleted=True,
        workflow_completed=True,
        lpar_uuid=LPAR_UUID,
        dry_run=False,
        steps=_workflow_steps(
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
            "unavailable_storage_source_count": 0,
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
        patch("hmc_mcp.server_tools.lpar.lifecycle.client_from_env", side_effect=fake_client_from_env),
        patch(
            "hmc_mcp.server_tools.lpar.lifecycle.decommission_lpar",
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

    from hmc_mcp.operations.lpar import decommission as ops

    monkeypatch.setattr(ops, "resolve_system_uuid", AsyncMock(return_value=SYSTEM_UUID))
    monkeypatch.setattr(ops, "authorize_decommission_lpar_ownership_snapshot", authorize)

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
async def test_decommission_warns_when_listed_vios_has_no_uuid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    hmc = _client()
    hmc.list_vios.return_value = [
        {"Resource": {"PartitionName": "vios-missing-id"}}
    ]
    _patch_common(monkeypatch, calls)

    result = await decommission_lpar(hmc, "system-a", "aix-prod", dry_run=True)

    assert result.blast_radius["storage_mappings"] == ()
    assert result.blast_radius["unresolved_storage_mapping_count"] == 0
    assert result.blast_radius["unavailable_storage_source_count"] == 1
    assert result.warnings == (
        "Storage blast radius may be incomplete: listed VIOS 'vios-missing-id' "
        "has no UUID, so its storage mappings could not be inventoried.",
    )
    hmc.get_vios_storage_detail.assert_not_awaited()


@pytest.mark.asyncio
async def test_decommission_warns_when_vios_storage_detail_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    hmc = _client()
    hmc.get_vios_storage_detail.return_value = None
    _patch_common(monkeypatch, calls)

    result = await decommission_lpar(hmc, "system-a", "aix-prod", dry_run=True)

    assert result.blast_radius["storage_mappings"] == ()
    assert result.blast_radius["unresolved_storage_mapping_count"] == 0
    assert result.blast_radius["unavailable_storage_source_count"] == 1
    assert result.warnings == (
        f"Storage blast radius may be incomplete: VIOS {VIOS_UUID!r} returned no "
        "storage detail, so its storage mappings could not be inventoried.",
    )
    hmc.get_vios_storage_detail.assert_awaited_once_with(VIOS_UUID)


@pytest.mark.asyncio
async def test_decommission_continues_when_vios_storage_detail_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    hmc = _client()
    hmc.get_vios_storage_detail.return_value = None
    _patch_common(monkeypatch, calls)

    async def list_adapters(
        _lpar_uuid: str, adapter_type: str
    ) -> list[dict[str, object]]:
        if adapter_type == "ClientNetworkAdapter":
            return [_adapter(adapter_type, "adapter-1")]
        return []

    hmc.list_adapters.side_effect = list_adapters
    hmc.submit_job.side_effect = lambda *args, **kwargs: calls.append("submit_job") or {
        "UUID": "job-uuid",
        "link": "/rest/api/uom/jobs/job-uuid",
    }
    hmc.wait_for_job.side_effect = lambda *args, **kwargs: calls.append("wait_for_job") or {
        "UUID": "job-uuid",
        "Resource": {"JobID": "job-uuid", "Status": "COMPLETED_OK"},
    }
    hmc.delete_adapter.side_effect = (
        lambda _lpar_uuid, adapter_type, adapter_uuid: calls.append(
            f"delete_adapter:{adapter_type}:{adapter_uuid}"
        )
    )
    hmc.delete_logical_partition.side_effect = lambda uuid: calls.append(
        f"delete_lpar:{uuid}"
    )

    result = await decommission_lpar(hmc, "system-a", "aix-prod")

    assert result.resource_deleted is True
    assert result.workflow_completed is True
    assert [step.status for step in result.steps] == ["ok", "ok", "ok"]
    assert result.blast_radius["unresolved_storage_mapping_count"] == 0
    assert result.blast_radius["unavailable_storage_source_count"] == 1
    assert result.warnings == (
        f"Storage blast radius may be incomplete: VIOS {VIOS_UUID!r} returned no "
        "storage detail, so its storage mappings could not be inventoried.",
    )
    assert calls == [
        "resolve_system_uuid:system-a",
        f"resolve_names:{SYSTEM_UUID}:system-a:{LPAR_UUID}",
        "authorize:system-a:aix-prod:False",
        "authorize:system-a:aix-prod:False",
        "submit_job",
        "wait_for_job",
        "delete_adapter:ClientNetworkAdapter:adapter-1",
        f"delete_lpar:{LPAR_UUID}",
    ]


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
        steps=_workflow_steps(
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
            "unavailable_storage_source_count": 0,
        },
    )
    assert calls == [
        "resolve_system_uuid:system-a",
        f"resolve_names:{SYSTEM_UUID}:system-a:{LPAR_UUID}",
        "authorize:system-a:aix-prod:False",
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

    from hmc_mcp.operations.lpar import decommission as ops

    monkeypatch.setattr(ops, "resolve_system_uuid", AsyncMock(return_value=SYSTEM_UUID))
    monkeypatch.setattr(
        ops,
        "resolve_lpar_ownership_names",
        AsyncMock(return_value=("system-a", "aix-prod")),
    )
    monkeypatch.setattr(
        ops,
        "authorize_decommission_lpar_ownership_snapshot",
        AsyncMock(side_effect=PermissionError("foreign owner")),
    )
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
async def test_decommission_override_reads_and_reports_both_ownership_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hmc = _client()
    hmc.config = HMCConfig(
        host="hmc.test", user="user", agent_id="alice", _env_file=None
    )

    from hmc_mcp.operations.lpar import decommission as ops

    monkeypatch.setattr(ops, "resolve_system_uuid", AsyncMock(return_value=SYSTEM_UUID))
    monkeypatch.setattr(
        ops,
        "resolve_lpar_ownership_names",
        AsyncMock(return_value=("system-a", "aix-prod")),
    )
    descriptions = AsyncMock(
        side_effect=(
            "[hmc-mcp owner:bob created:2026-08-14]",
            "[hmc-mcp owner:bob created:2026-08-14]",
        )
    )
    monkeypatch.setattr("hmc_mcp.operations.ownership.get_lpar_description", descriptions)

    result = await decommission_lpar(
        hmc, "system-a", "aix-prod", ownership_override=True
    )

    assert result.workflow_completed is True
    assert result.blast_radius["owner"] == "bob"
    assert descriptions.await_count == 2


@pytest.mark.asyncio
async def test_decommission_revalidates_changed_owner_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hmc = _client()
    hmc.config = HMCConfig(
        host="hmc.test", user="user", agent_id="alice", _env_file=None
    )

    from hmc_mcp.operations.lpar import decommission as ops

    monkeypatch.setattr(ops, "resolve_system_uuid", AsyncMock(return_value=SYSTEM_UUID))
    monkeypatch.setattr(
        ops,
        "resolve_lpar_ownership_names",
        AsyncMock(return_value=("system-a", "aix-prod")),
    )
    descriptions = AsyncMock(
        side_effect=(
            "[hmc-mcp owner:alice created:2026-08-14]",
            "[hmc-mcp owner:bob created:2026-08-15]",
        )
    )
    monkeypatch.setattr("hmc_mcp.operations.ownership.get_lpar_description", descriptions)

    with pytest.raises(PermissionError, match="owned by 'bob'"):
        await decommission_lpar(hmc, "system-a", "aix-prod")

    assert descriptions.await_count == 2
    hmc.submit_job.assert_not_awaited()
    hmc.delete_adapter.assert_not_awaited()
    hmc.delete_logical_partition.assert_not_awaited()


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
    hmc.get_quick_property.side_effect = (
        lambda *args, **kwargs: calls.append("get_state") or "not activated"
    )
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
    assert result.steps == _workflow_steps(
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
        "authorize:system-a:aix-prod:False",
        "submit_job",
        "wait_for_job",
        "get_state",
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

    async def list_adapters(
        _lpar_uuid: str, adapter_type: str
    ) -> list[dict[str, object]]:
        if adapter_type == "ClientNetworkAdapter":
            return [_adapter(adapter_type, "adapter-1")]
        return []

    hmc.list_adapters.side_effect = list_adapters
    hmc.get_quick_property.side_effect = (
        lambda *args, **kwargs: calls.append("get_state") or "not activated"
    )
    hmc.delete_adapter.side_effect = (
        lambda _lpar_uuid, adapter_type, adapter_uuid: calls.append(
            f"delete_adapter:{adapter_type}:{adapter_uuid}"
        )
    )
    hmc.delete_logical_partition.side_effect = lambda uuid: calls.append(
        f"delete_lpar:{uuid}"
    )

    result = await decommission_lpar(hmc, "system-a", "aix-prod")

    assert result.steps[0] == WorkflowStep(
        "power_off", "ok", {"already_off": True, "state": "not activated"}
    )
    hmc.submit_job.assert_not_awaited()
    hmc.wait_for_job.assert_not_awaited()
    assert calls == [
        "resolve_system_uuid:system-a",
        f"resolve_names:{SYSTEM_UUID}:system-a:{LPAR_UUID}",
        "authorize:system-a:aix-prod:False",
        "authorize:system-a:aix-prod:False",
        "get_state",
        "delete_adapter:ClientNetworkAdapter:adapter-1",
        f"delete_lpar:{LPAR_UUID}",
    ]


@pytest.mark.asyncio
async def test_decommission_stops_when_initially_off_lpar_restarts_before_detach(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    hmc = _client()
    hmc.get_logical_partition.return_value = _lpar(state="not activated")
    hmc.get_quick_property.return_value = "running"
    _patch_common(monkeypatch, calls)

    result = await decommission_lpar(hmc, "system-a", "aix-prod")

    assert result.workflow_completed is False
    assert result.steps == _workflow_steps(
        {
            "step": "power_off",
            "status": "ok",
            "result": {"already_off": True, "state": "not activated"},
        },
        {
            "step": "detach_adapters",
            "status": "error",
            "result": (
                "Cannot detach adapters from LPAR 'aix-prod': current state is "
                "'running'; expected 'not activated'."
            ),
        },
        {"step": "delete_lpar", "status": "skipped"},
    )
    hmc.get_quick_property.assert_awaited_once_with(
        "LogicalPartition", LPAR_UUID, "PartitionState"
    )
    hmc.submit_job.assert_not_awaited()
    hmc.delete_adapter.assert_not_awaited()
    hmc.delete_logical_partition.assert_not_awaited()


@pytest.mark.asyncio
async def test_decommission_stops_when_lpar_restarts_after_power_off_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    hmc = _client()
    hmc.get_quick_property.return_value = "running"
    _patch_common(monkeypatch, calls)

    result = await decommission_lpar(hmc, "system-a", "aix-prod")

    assert result.workflow_completed is False
    assert result.steps[0].status == "ok"
    assert result.steps[0].result["already_off"] is False
    assert result.steps[1] == WorkflowStep(
        "detach_adapters",
        "error",
        (
            "Cannot detach adapters from LPAR 'aix-prod': current state is "
            "'running'; expected 'not activated'."
        ),
    )
    assert result.steps[2] == WorkflowStep("delete_lpar", "skipped")
    hmc.submit_job.assert_awaited_once()
    hmc.wait_for_job.assert_awaited_once()
    hmc.get_quick_property.assert_awaited_once_with(
        "LogicalPartition", LPAR_UUID, "PartitionState"
    )
    hmc.delete_adapter.assert_not_awaited()
    hmc.delete_logical_partition.assert_not_awaited()


@pytest.mark.asyncio
async def test_decommission_stops_when_detach_state_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    hmc = _client()
    hmc.get_quick_property.side_effect = HMCError("state read failed")
    _patch_common(monkeypatch, calls)

    result = await decommission_lpar(hmc, "system-a", "aix-prod")

    assert result.workflow_completed is False
    assert result.steps[1] == WorkflowStep(
        "detach_adapters",
        "error",
        "Could not verify LPAR 'aix-prod' state before detaching adapters: state read failed",
    )
    assert result.steps[2] == WorkflowStep("delete_lpar", "skipped")
    hmc.delete_adapter.assert_not_awaited()
    hmc.delete_logical_partition.assert_not_awaited()


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
    assert result.steps[0].status == "error"
    assert fragment in result.steps[0].result
    assert result.steps[1:] == _workflow_steps(
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
    assert result.steps == _workflow_steps(
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
        "authorize:system-a:aix-prod:False",
        "delete_adapter:ClientNetworkAdapter:cna-1",
        "delete_adapter:ClientNetworkAdapter:cna-2",
        "delete_adapter:VirtualSCSIClientAdapter:vscsi-1",
    ]
    hmc.delete_logical_partition.assert_not_awaited()


@pytest.mark.asyncio
async def test_decommission_dry_run_makes_no_unclassified_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R18: the whole call set on the destructive tool's dry-run path.

    `hmc_decommission_lpar` is the one whose dry-run path a caller is most likely
    to trust, and the one that does the most before deciding: it builds the full
    blast radius — every adapter of four kinds, every VIOS on the system and its
    storage detail — before returning. Every one of those is a read, and pinning
    the set is what makes that a classification rather than an assumption.

    Two things happen on this path that a reader might not expect and that
    ADR 0039 records rather than hides: an SSH `lssyscfg` runs to read the
    ownership stamp, and with `ownership_override=True` a warning-level audit
    line is written locally. Neither is an HMC mutation; both are patched out
    here by `_patch_common`, which is why this test pins the REST surface only.
    """
    calls: list[str] = []
    hmc = _client()
    _patch_common(monkeypatch, calls)

    result = await decommission_lpar(hmc, "system-a", "aix-prod", dry_run=True)

    assert result.dry_run is True
    assert result.resource_deleted is False
    used = assert_only_these_client_methods_used(
        hmc,
        frozenset({
            "list_logical_partitions",  # read: find the partition on the system
            "get_logical_partition",  # read: its current state and attributes
            "list_adapters",  # read: blast radius, four adapter kinds
            "list_vios",  # read: every VIOS on the system
            "get_vios_storage_detail",  # read: each VIOS's storage mappings
        }),
    )
    assert used, "the handler touched nothing; the dry-run path was not exercised"
