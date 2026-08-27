"""Tests for attaching a new virtual disk to an existing LPAR."""

from unittest.mock import AsyncMock

import pytest

from conftest import assert_only_these_client_methods_used

from hmc_mcp.operations.assignments import WorkflowStep
from hmc_mcp.operations.provision import (
    AttachDiskResult,
    ProvisionStorage,
    attach_disk_to_lpar,
)


LPAR_UUID = "11111111-1111-1111-1111-111111111111"
VIOS_UUID = "22222222-2222-2222-2222-222222222222"
VG_UUID = "33333333-3333-3333-3333-333333333333"


@pytest.fixture(autouse=True)
def _authorize_lpar_mutations(monkeypatch):
    async def authorize(_hmc, lpar, _system, **_kwargs):
        return lpar

    monkeypatch.setattr(
        "hmc_mcp.operations.provision._resolve_and_authorize_lpar", authorize
    )


def _client() -> AsyncMock:
    client = AsyncMock()
    client.find_partition_by_name.return_value = {"UUID": LPAR_UUID}
    client.list_volume_groups.return_value = [{"UUID": VG_UUID}]
    return client


def _storage() -> ProvisionStorage:
    return ProvisionStorage(VIOS_UUID, "disk01", vg_uuid=VG_UUID)


@pytest.mark.asyncio
async def test_attach_disk_dry_run_validates_without_mutating() -> None:
    client = _client()

    result = await attach_disk_to_lpar(
        client,
        "existing-lpar",
        _storage(),
        capacity_mib=1024,
        vios_partition_id=2,
        vios_slot=10,
        dry_run=True,
    )

    assert result == AttachDiskResult(
        workflow_completed=False,
        lpar_uuid=LPAR_UUID,
        dry_run=True,
            steps=(
                WorkflowStep("create_disk", "dry_run"),
                WorkflowStep("vscsi", "dry_run"),
                WorkflowStep("storage", "dry_run"),
        ),
        warnings=(),
    )
    client.list_volume_groups.assert_awaited_once_with(VIOS_UUID)
    client.create_virtual_disk.assert_not_awaited()
    client.add_vscsi_adapter.assert_not_awaited()
    client.map_storage_to_lpar.assert_not_awaited()


@pytest.mark.asyncio
async def test_attach_disk_runs_shared_storage_leg_in_order() -> None:
    client = _client()
    calls: list[str] = []
    client.create_virtual_disk.side_effect = lambda *args: calls.append("create_disk")
    client.add_vscsi_adapter.side_effect = lambda *args: calls.append("vscsi")
    client.map_storage_to_lpar.side_effect = lambda *args: calls.append("storage")

    result = await attach_disk_to_lpar(
        client,
        LPAR_UUID,
        _storage(),
        capacity_mib=1024,
        vios_partition_id=2,
        vios_slot=10,
    )

    assert calls == ["create_disk", "vscsi", "storage"]
    assert result.workflow_completed is True
    assert [step.status for step in result.steps] == ["ok", "ok", "ok"]
    assert result.steps[0].result == {
        "disk_name": "disk01",
        "capacity_mb": 1024,
    }
    assert result.steps[1].result == {
        "lpar_uuid": LPAR_UUID,
        "vios_partition_id": 2,
        "vios_slot": 10,
    }
    assert result.steps[2].result == {
        "lpar_uuid": LPAR_UUID,
        "vios_uuid": VIOS_UUID,
        "storage_name": "disk01",
    }


@pytest.mark.asyncio
async def test_attach_disk_reports_partial_failure_and_skips_remainder() -> None:
    from hmc_mcp.errors import HMCError

    client = _client()
    client.add_vscsi_adapter.side_effect = HMCError("adapter failed")

    result = await attach_disk_to_lpar(
        client,
        LPAR_UUID,
        _storage(),
        capacity_mib=1024,
        vios_partition_id=2,
        vios_slot=10,
    )

    assert [step.status for step in result.steps] == ["ok", "error", "skipped"]
    assert result.workflow_completed is False
    client.map_storage_to_lpar.assert_not_awaited()


@pytest.mark.asyncio
async def test_attach_disk_rejects_invalid_capacity_before_mutating() -> None:
    client = _client()

    with pytest.raises(ValueError, match="capacity_mib must be greater than zero"):
        await attach_disk_to_lpar(
            client,
            LPAR_UUID,
            _storage(),
            capacity_mib=0,
            vios_partition_id=2,
            vios_slot=10,
        )

    client.create_virtual_disk.assert_not_awaited()


@pytest.mark.asyncio
async def test_attach_disk_dry_run_makes_no_unclassified_call() -> None:
    """R18: the whole call set is pinned, not three negatives.

    The test above names the three mutations it expects not to happen. That
    stays green if a fourth is added, which is the inference epic #218
    requirement 5 refuses. Here every method the handler touched is read back and
    compared against the classified read-only set.
    """
    client = _client()

    await attach_disk_to_lpar(
        client,
        "existing-lpar",
        _storage(),
        capacity_mib=1024,
        vios_partition_id=2,
        vios_slot=10,
        dry_run=True,
    )

    used = assert_only_these_client_methods_used(
        client,
        frozenset({
            "find_partition_by_name",  # read: resolve the LPAR name to a UUID
            "get_logical_partition",  # read: UUID pass-through validation
            "list_volume_groups",  # read: volume-group precondition check
        }),
    )
    assert used, "the handler touched nothing; the dry-run path was not exercised"
