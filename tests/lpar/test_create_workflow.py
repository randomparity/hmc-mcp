"""Direct contracts for the shared LPAR creation workflow."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from hmc_mcp.client.core import HMCClient
from hmc_mcp.operations.lpar.assignments import (
    AssignmentResult,
    LparPcieAssignments,
)
from hmc_mcp.operations.lpar.core import LparCreation, LparCreationResult
from hmc_mcp.operations.lpar.workflow_contract import WorkflowStep
from hmc_mcp.operations.lpar.workflows import create_lpar


def _creation() -> LparCreation:
    return cast(LparCreation, SimpleNamespace(name="app-lpar"))


@pytest.mark.asyncio
async def test_create_lpar_returns_create_step_when_creation_has_no_resource(monkeypatch):
    hmc = cast(HMCClient, object())
    assignments = LparPcieAssignments()
    prevalidate = AsyncMock()
    create = AsyncMock(
        return_value=LparCreationResult(True, None, False, ("create warning",))
    )
    apply = AsyncMock()
    monkeypatch.setattr(
        "hmc_mcp.operations.lpar.workflows.prevalidate_lpar_pcie_assignments",
        prevalidate,
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.lpar.workflows.create_and_stamp_lpar", create
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.lpar.workflows.apply_validated_lpar_pcie_assignments",
        apply,
    )

    result = await create_lpar(hmc, "sys1", _creation(), assignments)

    assert result.resource_created is True
    assert result.workflow_completed is False
    assert result.steps == (WorkflowStep("create", "ok", None),)
    assert result.warnings == ("create warning",)
    prevalidate.assert_awaited_once_with(hmc, "sys1", assignments)
    apply.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_lpar_appends_assignment_steps(monkeypatch):
    hmc = cast(HMCClient, object())
    assignments = LparPcieAssignments()
    lpar = {"UUID": "lpar-1"}
    monkeypatch.setattr(
        "hmc_mcp.operations.lpar.workflows.prevalidate_lpar_pcie_assignments",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.lpar.workflows.create_and_stamp_lpar",
        AsyncMock(return_value=LparCreationResult(True, lpar, True, ())),
    )
    apply = AsyncMock(
        return_value=AssignmentResult(
            True, False, (WorkflowStep("vnic[0]", "ok", {"slot": 4}),)
        )
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.lpar.workflows.apply_validated_lpar_pcie_assignments",
        apply,
    )

    result = await create_lpar(hmc, "sys1", _creation(), assignments)

    assert result.workflow_completed is True
    assert result.lpar is lpar
    assert result.steps == (
        WorkflowStep("create", "ok", lpar),
        WorkflowStep("vnic[0]", "ok", {"slot": 4}),
    )
    apply.assert_awaited_once_with(hmc, "sys1", "app-lpar", assignments)
