"""Partial-state contracts for LPAR modification workflows."""

from unittest.mock import AsyncMock

import pytest

from hmc_mcp.documents import LparResources
from hmc_mcp.errors import HMCError
from hmc_mcp.operations.lpar.assignments import LparPcieAssignments
from hmc_mcp.operations.lpar.dlpar import modify_lpar


@pytest.mark.asyncio
async def test_modify_lpar_returns_rename_when_resource_update_fails(monkeypatch):
    hmc = AsyncMock()
    hmc.modify_logical_partition.side_effect = [
        {"UUID": "lpar-1", "PartitionName": "renamed"},
        HMCError("resource update failed", 500),
    ]
    monkeypatch.setattr(
        "hmc_mcp.operations.lpar.dlpar.resolve_and_authorize_lpar_mutation",
        AsyncMock(return_value="lpar-1"),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.lpar.dlpar.prevalidate_lpar_pcie_assignments",
        AsyncMock(),
    )

    result = await modify_lpar(
        hmc,
        "system-1",
        "lpar-1",
        LparResources(desired_memory=8192),
        LparPcieAssignments(),
        new_name="renamed",
    )

    assert result.workflow_completed is False
    assert result.lpar == {"UUID": "lpar-1", "PartitionName": "renamed"}
    assert [(step.step, step.status) for step in result.steps] == [
        ("rename", "ok"),
        ("resources", "error"),
    ]
    assert result.warnings == ("resource update failed (HTTP 500)",)
