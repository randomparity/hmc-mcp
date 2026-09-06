"""Direct contracts for cluster logical-unit operation orchestration."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hmc_mcp.operations.storage.cluster import create_logical_unit, delete_logical_unit


@pytest.mark.asyncio
async def test_logical_unit_operations_delegate_submission_and_waiting(monkeypatch):
    hmc = SimpleNamespace(
        create_logical_unit=AsyncMock(return_value={"UUID": "create-job"}),
        delete_logical_unit=AsyncMock(return_value={"UUID": "delete-job"}),
    )
    waited = AsyncMock(side_effect=lambda _hmc, job, *_: job)
    monkeypatch.setattr("hmc_mcp.operations.storage.cluster.wait_for_submitted_job", waited)

    created = await create_logical_unit(
        hmc, "cluster-1", "data", 20, "THIN", "VirtualIO_Disk", cloned_from="source"
    )
    deleted = await delete_logical_unit(hmc, "cluster-1", "lu-1")

    assert created == {"UUID": "create-job"}
    assert deleted == {"UUID": "delete-job"}
    hmc.create_logical_unit.assert_awaited_once_with(
        "cluster-1",
        "data",
        20,
        lu_type="THIN",
        device_type="VirtualIO_Disk",
        cloned_from="source",
    )
    hmc.delete_logical_unit.assert_awaited_once_with("cluster-1", "lu-1")
    assert waited.await_count == 2
