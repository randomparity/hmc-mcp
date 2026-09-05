"""Partial-state contracts for LPAR modification workflows."""

from unittest.mock import AsyncMock

import pytest

from hmc_mcp.documents import LparResources
from hmc_mcp.errors import HMCError
from hmc_mcp.operations.lpar.assignments import (
    LparPcieAssignments,
    LparPcieWorkflowResult,
)
from hmc_mcp.operations.lpar.dlpar import modify_lpar
from hmc_mcp.operations.lpar.workflow_contract import WorkflowStep


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


@pytest.mark.asyncio
async def test_modify_lpar_propagates_resource_failure_without_partial_state(monkeypatch):
    hmc = AsyncMock()
    hmc.modify_logical_partition.side_effect = HMCError("resource update failed", 500)
    monkeypatch.setattr(
        "hmc_mcp.operations.lpar.dlpar.resolve_and_authorize_lpar_mutation",
        AsyncMock(return_value="lpar-1"),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.lpar.dlpar.prevalidate_lpar_pcie_assignments",
        AsyncMock(),
    )

    with pytest.raises(HMCError, match="resource update failed"):
        await modify_lpar(
            hmc,
            "system-1",
            "lpar-1",
            LparResources(desired_memory=8192),
            LparPcieAssignments(),
        )


@pytest.mark.asyncio
async def test_modify_lpar_preserves_steps_when_final_readback_fails(monkeypatch):
    hmc = AsyncMock()
    hmc.get_logical_partition.side_effect = HMCError("readback unavailable", 503)
    monkeypatch.setattr(
        "hmc_mcp.operations.lpar.dlpar.resolve_and_authorize_lpar_mutation",
        AsyncMock(return_value="lpar-1"),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.lpar.dlpar.prevalidate_lpar_pcie_assignments",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.lpar.dlpar.apply_validated_lpar_pcie_assignments",
        AsyncMock(
            return_value=LparPcieWorkflowResult(
                False,
                True,
                None,
                None,
                (WorkflowStep("assign", "ok"),),
                (),
            )
        ),
    )

    result = await modify_lpar(
        hmc,
        "system-1",
        "lpar-1",
        LparResources(),
        LparPcieAssignments(),
    )

    assert result.workflow_completed is True
    assert [(step.step, step.status) for step in result.steps] == [("assign", "ok")]
    assert result.warnings == ("final LPAR readback failed: readback unavailable (HTTP 503)",)
