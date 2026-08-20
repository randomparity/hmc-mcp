"""Presentation contract tests for verified vNIC workflows."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from hmc_mcp.operations_ssh_network import VnicBackingSelector, VnicChangeResult
from hmc_mcp.server import hmc_add_vnic, hmc_remove_vnic


def _result(operation: str, slot_num: str) -> VnicChangeResult:
    return VnicChangeResult(
        operation=operation,  # type: ignore[arg-type]
        mutation_dispatched=True,
        changed=True,
        selector=None,
        slot_num=slot_num,
        vnic_before=(),
        backing_before=(),
        vnic_after=(),
        backing_after=(),
        vnic_after_read_succeeded=True,
        backing_after_read_succeeded=True,
        output="done",
        errors=(),
    )


def test_add_vnic_builds_typed_selector(monkeypatch) -> None:
    monkeypatch.setenv("HMC_HOST", "h")
    monkeypatch.setenv("HMC_USER", "u")
    monkeypatch.setenv("HMC_PASSWORD", "p")
    operation = AsyncMock(return_value=_result("add", "4"))
    monkeypatch.setattr("hmc_mcp.server_network.add_vnic", operation)
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr("hmc_mcp.server_network.client_from_env", lambda _profile: client)

    result = hmc_add_vnic(
        "system",
        "lpar",
        vios_name="vios1",
        vios_lpar_id="2",
        adapter_id="1",
        physical_port_id="0",
        capacity_percent=20.25,
        port_vlan_id=100,
    )

    args = operation.await_args.args
    assert args[1:3] == ("system", "lpar")
    assert args[3] == VnicBackingSelector(
        "vios1", "2", "1", "0", Decimal("20.25")
    )
    assert args[4] == 100
    assert result["slot_num"] == "4"
    assert result["changed"] is True


def test_remove_vnic_uses_slot_num(monkeypatch) -> None:
    monkeypatch.setenv("HMC_HOST", "h")
    monkeypatch.setenv("HMC_USER", "u")
    monkeypatch.setenv("HMC_PASSWORD", "p")
    operation = AsyncMock(return_value=_result("remove", "4"))
    monkeypatch.setattr("hmc_mcp.server_network.remove_vnic", operation)
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr("hmc_mcp.server_network.client_from_env", lambda _profile: client)

    result = hmc_remove_vnic("system", "lpar", slot_num="4")

    assert operation.await_args.args[1:] == ("system", "lpar", "4")
    assert result["operation"] == "remove"
