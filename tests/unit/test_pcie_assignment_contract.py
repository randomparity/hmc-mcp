"""Safe dedicated PCIe assignment contract tests."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from hmc_mcp.config import HMCConfig
from hmc_mcp.operations_pcie import (
    PcieAssignmentUnavailableError,
    assign_dedicated_pcie_slot,
    unassign_dedicated_pcie_slot,
)
from hmc_mcp.ssh_commands import assign_profile_io_slot, unassign_profile_io_slot
from hmc_mcp.server_profiles import tool_security


def _config() -> HMCConfig:
    return HMCConfig(host="h", user="u", password="p", verify_ssl=False, _env_file=None)


@pytest.mark.parametrize(
    ("operation", "token"),
    [(assign_profile_io_slot, "io_slots+="), (unassign_profile_io_slot, "io_slots-=")],
)
def test_profile_commands_are_symmetric_and_never_force(monkeypatch, operation, token):
    command = AsyncMock(return_value="ok")
    monkeypatch.setattr("hmc_mcp.ssh_commands.run_hmc_command", command)

    assert asyncio.run(operation(_config(), "sys", "lpar", "profile", "123")) == "ok"
    built = command.await_args.args[1]
    assert token in built
    assert "123//0" in built
    assert "--force" not in built


def test_assignment_rejects_before_mutation_when_profile_readback_is_unavailable(
    monkeypatch,
):
    hmc = AsyncMock()
    hmc.config = _config()
    monkeypatch.setattr(
        "hmc_mcp.operations_pcie.resolve_system_uuid", AsyncMock(return_value="sys-uuid")
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_pcie.resolve_lpar_uuid", AsyncMock(return_value="lpar-uuid")
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_pcie.resolve_lpar_ownership_names",
        AsyncMock(return_value=("sys", "lpar")),
    )
    authorize = AsyncMock()
    inventory = AsyncMock()
    monkeypatch.setattr("hmc_mcp.operations_pcie.authorize_lpar_mutation", authorize)
    monkeypatch.setattr("hmc_mcp.operations_pcie.list_dedicated_slots", inventory)

    with pytest.raises(PcieAssignmentUnavailableError, match="profile readback"):
        asyncio.run(assign_dedicated_pcie_slot(hmc, "sys", "lpar", "prof", "123"))

    authorize.assert_awaited_once_with(hmc, "sys", "lpar", ownership_override=False)
    inventory.assert_not_awaited()


def test_unassignment_passes_explicit_ownership_override(monkeypatch):
    hmc = AsyncMock()
    hmc.config = _config()
    monkeypatch.setattr(
        "hmc_mcp.operations_pcie.resolve_system_uuid", AsyncMock(return_value="sys-uuid")
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_pcie.resolve_lpar_uuid", AsyncMock(return_value="lpar-uuid")
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_pcie.resolve_lpar_ownership_names",
        AsyncMock(return_value=("sys", "lpar")),
    )
    authorize = AsyncMock()
    monkeypatch.setattr("hmc_mcp.operations_pcie.authorize_lpar_mutation", authorize)

    with pytest.raises(PcieAssignmentUnavailableError):
        asyncio.run(
            unassign_dedicated_pcie_slot(
                hmc, "sys", "lpar", "prof", "123", ownership_override=True
            )
        )

    authorize.assert_awaited_once_with(hmc, "sys", "lpar", ownership_override=True)


def test_mcp_contract_replaces_the_unsafe_profile_tool():
    security = tool_security()
    assert "hmc_assign_profile_io_slot" not in security
    assert security["hmc_assign_dedicated_pcie_slot"].operation == (
        "pcie.assign_dedicated_slot"
    )
    assert security["hmc_unassign_dedicated_pcie_slot"].operation == (
        "pcie.unassign_dedicated_slot"
    )
    assert security["hmc_assign_dedicated_pcie_slot"].effect == "mutate"
    assert security["hmc_unassign_dedicated_pcie_slot"].target_kind == "lpar"
