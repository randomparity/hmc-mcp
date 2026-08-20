from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from hmc_mcp.config import HMCConfig
from hmc_mcp.operations_pcie import (
    SriovLogicalPortCapabilityError,
    assign_sriov_logical_port,
    unassign_sriov_logical_port,
)


def _hmc() -> AsyncMock:
    hmc = AsyncMock()
    hmc.config = HMCConfig(host="h", user="u", password="p", _env_file=None)
    return hmc


def _common(monkeypatch, *, state="Not Activated", rmc="inactive", configured=()):
    monkeypatch.setattr(
        "hmc_mcp.operations_pcie.resolve_system_uuid", AsyncMock(return_value="su")
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_pcie.resolve_lpar_uuid", AsyncMock(return_value="lu")
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_pcie.resolve_lpar_ownership_names",
        AsyncMock(return_value=("sys", "lpar")),
    )
    monkeypatch.setattr("hmc_mcp.operations_pcie.authorize_lpar_mutation", AsyncMock())
    monkeypatch.setattr(
        "hmc_mcp.operations_pcie.list_sriov_adapter_rows",
        AsyncMock(
            return_value=[
                {
                    "adapter_id": "1",
                    "config_state": "sriov",
                    "functional_state": "1",
                    "sriov_status": "running",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_pcie.list_sriov_physical_port_rows",
        AsyncMock(
            return_value=[
                {
                    "adapter_id": "1",
                    "phys_port_id": "0",
                    "state": "1",
                    "phys_port_loc": "U-P1-C4-T1",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_pcie.list_sriov_configured_logical_port_rows",
        AsyncMock(return_value=list(configured)),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_pcie.list_sriov_unconfigured_logical_port_rows",
        AsyncMock(
            return_value=[
                {
                    "adapter_id": "1",
                    "logical_port_id": "3",
                    "logical_port_type": "unconfigured",
                    "location_code": "U-P1-C4-T1-S3",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_pcie.read_sriov_lpar_state",
        AsyncMock(
            return_value={
                "name": "lpar",
                "lpar_id": "2",
                "state": state,
                "rmc_state": rmc,
            }
        ),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_pcie.read_sriov_profile_ports",
        AsyncMock(return_value={"name": "prof", "sriov_eth_logical_ports": "none"}),
    )


@pytest.mark.asyncio
async def test_assign_mutates_and_verifies_effective_readback(monkeypatch):
    _common(monkeypatch)
    mutate = AsyncMock(return_value="")
    monkeypatch.setattr(
        "hmc_mcp.operations_pcie.assign_sriov_logical_port_dynamic", mutate
    )
    after = {
        "config_id": "0",
        "lpar_name": "lpar",
        "lpar_id": "2",
        "lpar_state": "Not Activated",
        "adapter_id": "1",
        "logical_port_id": "3",
        "phys_port_id": "0",
        "functional_state": "1",
        "capacity": "2.0",
        "max_capacity": "100.0",
    }
    rows = AsyncMock(side_effect=[[], [after]])
    monkeypatch.setattr(
        "hmc_mcp.operations_pcie.list_sriov_configured_logical_port_rows", rows
    )

    result = await assign_sriov_logical_port(
        _hmc(), "sys", "lpar", "1", "0", "3", Decimal("2.0")
    )

    assert result.changed is True
    assert result.effective_after.owner_lpar == "lpar"
    mutate.assert_awaited_once()


@pytest.mark.asyncio
async def test_assign_is_idempotent_and_refuses_foreign_owner(monkeypatch):
    owned = {
        "config_id": "0",
        "lpar_name": "lpar",
        "lpar_id": "2",
        "lpar_state": "Not Activated",
        "adapter_id": "1",
        "logical_port_id": "3",
        "phys_port_id": "0",
        "functional_state": "1",
        "capacity": "2.0",
        "max_capacity": "100.0",
    }
    _common(monkeypatch, configured=[owned])
    mutate = AsyncMock()
    monkeypatch.setattr(
        "hmc_mcp.operations_pcie.assign_sriov_logical_port_dynamic", mutate
    )
    unchanged = await assign_sriov_logical_port(
        _hmc(), "sys", "lpar", "1", "0", "3", Decimal("2.0")
    )
    assert unchanged.changed is False
    mutate.assert_not_awaited()

    owned["lpar_name"] = "other"
    with pytest.raises(PermissionError, match="already assigned"):
        await assign_sriov_logical_port(
            _hmc(), "sys", "lpar", "1", "0", "3", Decimal("2.0")
        )


@pytest.mark.asyncio
async def test_assign_rejects_capacity_and_unsupported_running_state(monkeypatch):
    _common(monkeypatch, state="Running", rmc="inactive")
    with pytest.raises(SriovLogicalPortCapabilityError, match="active RMC"):
        await assign_sriov_logical_port(
            _hmc(), "sys", "lpar", "1", "0", "3", Decimal("2.0")
        )
    with pytest.raises(ValueError, match="between 1 and 100"):
        await assign_sriov_logical_port(
            _hmc(), "sys", "lpar", "1", "0", "3", Decimal("0.5")
        )


@pytest.mark.asyncio
async def test_profile_unassign_is_idempotent_and_verified(monkeypatch):
    _common(monkeypatch)
    mutate = AsyncMock(return_value="")
    monkeypatch.setattr(
        "hmc_mcp.operations_pcie.unassign_sriov_logical_port_profile", mutate
    )
    unchanged = await unassign_sriov_logical_port(
        _hmc(), "sys", "lpar", "prof", "1", "0", "3"
    )
    assert unchanged.changed is False

    record = "0:1:0:3:0:0:0:all::all:0:0:2.0:100.0:none:0::::"
    reads = AsyncMock(
        side_effect=[
            {"name": "prof", "sriov_eth_logical_ports": record},
            {"name": "prof", "sriov_eth_logical_ports": "none"},
        ]
    )
    monkeypatch.setattr("hmc_mcp.operations_pcie.read_sriov_profile_ports", reads)
    changed = await unassign_sriov_logical_port(
        _hmc(), "sys", "lpar", "prof", "1", "0", "3"
    )
    assert changed.changed is True
    mutate.assert_awaited_once()
