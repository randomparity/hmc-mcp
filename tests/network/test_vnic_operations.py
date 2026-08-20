from dataclasses import fields
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from hmc_mcp.operations_ssh_network import (
    VnicBackingSelector,
    VnicCapabilityError,
    VnicChangeResult,
    VnicPartialError,
    add_vnic,
    remove_vnic,
)
from hmc_mcp.config import HMCConfig


def test_vnic_models_are_immutable_and_result_field_order_is_stable() -> None:
    selector = VnicBackingSelector("vios-a", "100", "1", "1", Decimal("2.0"))
    with pytest.raises(AttributeError):
        selector.adapter_id = "2"  # ty: ignore[invalid-assignment]
    assert [field.name for field in fields(VnicChangeResult)] == [
        "operation",
        "mutation_dispatched",
        "changed",
        "selector",
        "slot_num",
        "vnic_before",
        "backing_before",
        "vnic_after",
        "backing_after",
        "vnic_after_read_succeeded",
        "backing_after_read_succeeded",
        "output",
        "errors",
    ]


def _hmc() -> AsyncMock:
    hmc = AsyncMock()
    hmc.config = HMCConfig(
        host="h",
        user="u",
        password="p",
        _env_file=None,  # ty: ignore[unknown-argument]
    )
    return hmc


def _vnic(
    slot: str = "2", vlan: str = "7", logical: str = "3", **changes: str
) -> dict[str, str]:
    backing = {
        "vios_name": "vios-a",
        "vios_lpar_id": "100",
        "adapter_id": "1",
        "physical_port_id": "1",
        "logical_port_id": logical,
        "capacity": "2.0",
        "desired_capacity": "2.0",
    }
    backing.update(changes)
    return {
        "lpar_name": "client-a",
        "lpar_id": "3",
        "slot_num": slot,
        "port_vlan_id": vlan,
        "backing_devices": (
            "sriov/{vios_name}/{vios_lpar_id}/{adapter_id}/{physical_port_id}/"
            "{logical_port_id}/{capacity}/{desired_capacity}/50/100/100"
        ).format_map(backing),
    }


def _backing(logical: str = "3", **changes: str) -> dict[str, str]:
    row = {
        "lpar_name": "vios-a",
        "lpar_id": "100",
        "type": "sriov",
        "adapter_id": "1",
        "physical_port_id": "1",
        "logical_port_id": logical,
        "capacity": "2.0",
        "desired_capacity": "2.0",
        "max_capacity": "100",
        "desired_max_capacity": "100",
        "failover_priority": "50",
        "is_active": "1",
        "status": "Operational",
    }
    row.update(changes)
    return row


def _common(monkeypatch: pytest.MonkeyPatch) -> None:
    module = "hmc_mcp.operations_ssh_network"
    monkeypatch.setattr(f"{module}.resolve_system_uuid", AsyncMock(return_value="su"))
    monkeypatch.setattr(f"{module}.resolve_lpar_uuid", AsyncMock(return_value="lu"))
    monkeypatch.setattr(
        f"{module}.resolve_lpar_ownership_names",
        AsyncMock(return_value=("system-a", "client-a")),
    )
    monkeypatch.setattr(f"{module}.authorize_lpar_mutation", AsyncMock())
    monkeypatch.setattr(f"{module}._require_admitted_environment", AsyncMock())
    monkeypatch.setattr(
        f"{module}.read_vios_identity",
        AsyncMock(
            return_value={"name": "vios-a", "lpar_id": "100", "lpar_env": "vioserver"}
        ),
    )
    monkeypatch.setattr(
        f"{module}.list_sriov_adapter_rows",
        AsyncMock(
            return_value=[
                {"adapter_id": "1", "config_state": "sriov", "functional_state": "1"}
            ]
        ),
    )
    monkeypatch.setattr(
        f"{module}.list_sriov_physical_port_rows",
        AsyncMock(
            return_value=[{"adapter_id": "1", "phys_port_id": "1", "state": "1"}]
        ),
    )
    monkeypatch.setattr(
        f"{module}.list_sriov_configured_logical_port_rows", AsyncMock(return_value=[])
    )


@pytest.mark.parametrize(
    "field", ["vios_name", "vios_lpar_id", "adapter_id", "physical_port_id"]
)
@pytest.mark.asyncio
async def test_add_rejects_blank_selector_fields(field: str) -> None:
    values = {
        "vios_name": "vios-a",
        "vios_lpar_id": "100",
        "adapter_id": "1",
        "physical_port_id": "1",
    }
    values[field] = " "
    selector = VnicBackingSelector(**values, capacity_percent=Decimal("2"))
    with pytest.raises(ValueError, match="must not be blank"):
        await add_vnic(_hmc(), "system-a", "client-a", selector, 7)


@pytest.mark.parametrize(
    "capacity", [Decimal("0"), Decimal("101"), Decimal("NaN"), Decimal("1.001")]
)
@pytest.mark.asyncio
async def test_add_rejects_invalid_capacity(capacity: Decimal) -> None:
    selector = VnicBackingSelector("vios-a", "100", "1", "1", capacity)
    with pytest.raises(ValueError):
        await add_vnic(_hmc(), "system-a", "client-a", selector, 7)


@pytest.mark.parametrize("vlan", [-1, 4095])
@pytest.mark.asyncio
async def test_add_rejects_vlan_out_of_range(vlan: int) -> None:
    selector = VnicBackingSelector("vios-a", "100", "1", "1", Decimal("2"))
    with pytest.raises(ValueError, match="between 0 and 4094"):
        await add_vnic(_hmc(), "system-a", "client-a", selector, vlan)


@pytest.mark.parametrize("character", ["/", ",", "=", '"', "\n"])
@pytest.mark.asyncio
async def test_add_rejects_each_nested_or_record_delimiter(character: str) -> None:
    selector = VnicBackingSelector(f"vios{character}a", "100", "1", "1", Decimal("2"))
    with pytest.raises(ValueError):
        await add_vnic(_hmc(), "system-a", "client-a", selector, 7)


@pytest.mark.asyncio
async def test_add_verified_retry_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    _common(monkeypatch)
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.list_vnic_rows",
        AsyncMock(return_value=[_vnic()]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.list_vnic_backing_rows",
        AsyncMock(return_value=[_backing()]),
    )
    mutate = AsyncMock()
    monkeypatch.setattr("hmc_mcp.operations_ssh_network.add_vnic_backing", mutate)
    result = await add_vnic(
        _hmc(),
        "system-a",
        "client-a",
        VnicBackingSelector("vios-a", "100", "1", "1", Decimal("2")),
        7,
    )
    assert (result.changed, result.slot_num, result.mutation_dispatched) == (
        False,
        "2",
        False,
    )
    mutate.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_successfully_correlates_new_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _common(monkeypatch)
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.list_vnic_rows",
        AsyncMock(side_effect=[[], [_vnic()]]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.list_vnic_backing_rows",
        AsyncMock(side_effect=[[], [_backing()]]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.add_vnic_backing",
        AsyncMock(return_value="created"),
    )
    result = await add_vnic(
        _hmc(),
        "system-a",
        "client-a",
        VnicBackingSelector("vios-a", "100", "1", "1", Decimal("2")),
        7,
    )
    assert (result.changed, result.slot_num, result.output) == (True, "2", "created")


@pytest.mark.asyncio
async def test_add_unchanged_reads_after_dispatch_are_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _common(monkeypatch)
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.list_vnic_rows",
        AsyncMock(side_effect=[[], []]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.list_vnic_backing_rows",
        AsyncMock(side_effect=[[], []]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.add_vnic_backing", AsyncMock()
    )

    with pytest.raises(VnicPartialError) as caught:
        await add_vnic(
            _hmc(),
            "system-a",
            "client-a",
            VnicBackingSelector("vios-a", "100", "1", "1", Decimal("2")),
            7,
        )

    result = caught.value.result
    assert result.mutation_dispatched
    assert result.changed is None
    assert result.slot_num is None
    assert result.vnic_after == ()
    assert result.backing_after == ()


@pytest.mark.asyncio
async def test_add_retry_refuses_extra_selector_matching_degraded_backing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _common(monkeypatch)
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.list_vnic_rows",
        AsyncMock(return_value=[_vnic()]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.list_vnic_backing_rows",
        AsyncMock(return_value=[_backing(), _backing(logical="4", is_active="0")]),
    )
    mutate = AsyncMock()
    monkeypatch.setattr("hmc_mcp.operations_ssh_network.add_vnic_backing", mutate)

    with pytest.raises(VnicCapabilityError, match="ambiguous or degraded"):
        await add_vnic(
            _hmc(),
            "system-a",
            "client-a",
            VnicBackingSelector("vios-a", "100", "1", "1", Decimal("2")),
            7,
        )

    mutate.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_final_refuses_extra_selector_matching_degraded_backing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _common(monkeypatch)
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.list_vnic_rows",
        AsyncMock(side_effect=[[], [_vnic()]]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.list_vnic_backing_rows",
        AsyncMock(
            side_effect=[[], [_backing(), _backing(logical="4", status="Degraded")]]
        ),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.add_vnic_backing",
        AsyncMock(return_value="created"),
    )

    with pytest.raises(VnicPartialError) as caught:
        await add_vnic(
            _hmc(),
            "system-a",
            "client-a",
            VnicBackingSelector("vios-a", "100", "1", "1", Decimal("2")),
            7,
        )

    result = caught.value.result
    assert result.changed is None
    assert result.slot_num == "2"
    assert len(result.backing_after) == 2
    assert {item.status for item in result.backing_after} == {
        "Operational",
        "Degraded",
    }


@pytest.mark.asyncio
async def test_add_rejects_two_new_matching_vnics_despite_one_operational_backing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _common(monkeypatch)
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.list_vnic_rows",
        AsyncMock(side_effect=[[], [_vnic(), _vnic(slot="3", logical="4")]]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.list_vnic_backing_rows",
        AsyncMock(side_effect=[[], [_backing()]]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.add_vnic_backing",
        AsyncMock(return_value="created"),
    )

    with pytest.raises(VnicPartialError) as caught:
        await add_vnic(
            _hmc(),
            "system-a",
            "client-a",
            VnicBackingSelector("vios-a", "100", "1", "1", Decimal("2")),
            7,
        )

    result = caught.value.result
    assert result.changed is None
    assert result.slot_num is None
    assert tuple(item.slot_num for item in result.vnic_after) == ("2", "3")
    assert result.vnic_after_read_succeeded
    assert result.backing_after_read_succeeded


@pytest.mark.asyncio
async def test_add_successful_reads_with_only_new_vnic_are_contradictory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _common(monkeypatch)
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.list_vnic_rows",
        AsyncMock(side_effect=[[], [_vnic()]]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.list_vnic_backing_rows",
        AsyncMock(side_effect=[[], []]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.add_vnic_backing", AsyncMock()
    )

    with pytest.raises(VnicPartialError) as caught:
        await add_vnic(
            _hmc(),
            "system-a",
            "client-a",
            VnicBackingSelector("vios-a", "100", "1", "1", Decimal("2")),
            7,
        )

    result = caught.value.result
    assert result.changed is None
    assert result.slot_num == "2"
    assert result.vnic_after_read_succeeded
    assert result.backing_after_read_succeeded
    assert len(result.vnic_after) == 1
    assert result.backing_after == ()


@pytest.mark.asyncio
async def test_add_command_and_both_read_failures_are_retained_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _common(monkeypatch)
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.list_vnic_rows",
        AsyncMock(side_effect=[[], TimeoutError("vnic read")]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.list_vnic_backing_rows",
        AsyncMock(side_effect=[[], OSError("backing read")]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.add_vnic_backing",
        AsyncMock(side_effect=RuntimeError("HMC rejected VLAN")),
    )
    with pytest.raises(VnicPartialError) as caught:
        await add_vnic(
            _hmc(),
            "system-a",
            "client-a",
            VnicBackingSelector("vios-a", "100", "1", "1", Decimal("2")),
            7,
        )
    result = caught.value.result
    assert result.changed is None
    assert (
        not result.vnic_after_read_succeeded and not result.backing_after_read_succeeded
    )
    assert "HMC rejected VLAN" in result.errors[0]
    assert "vnic read" in result.errors[1]
    assert "backing read" in result.errors[2]


@pytest.mark.asyncio
async def test_remove_absent_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    _common(monkeypatch)
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.list_vnic_rows", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.list_vnic_backing_rows",
        AsyncMock(return_value=[]),
    )
    result = await remove_vnic(_hmc(), "system-a", "client-a", "2")
    assert (result.changed, result.selector, result.slot_num) == (False, None, "2")


@pytest.mark.asyncio
async def test_remove_success_preserves_captured_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _common(monkeypatch)
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.list_vnic_rows",
        AsyncMock(side_effect=[[_vnic()], []]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.list_vnic_backing_rows",
        AsyncMock(side_effect=[[_backing()], []]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.remove_vnic_slot",
        AsyncMock(return_value="removed"),
    )
    result = await remove_vnic(_hmc(), "system-a", "client-a", "2")
    assert result.changed is True
    assert result.selector == VnicBackingSelector(
        "vios-a", "100", "1", "1", Decimal("2.0")
    )


@pytest.mark.parametrize(
    "rows", [[], [_backing(is_active="0")], [_backing(), _backing()]]
)
@pytest.mark.asyncio
async def test_remove_refuses_uncorrelated_or_degraded_backing(
    monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, str]]
) -> None:
    _common(monkeypatch)
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.list_vnic_rows",
        AsyncMock(return_value=[_vnic()]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.list_vnic_backing_rows",
        AsyncMock(return_value=rows),
    )
    with pytest.raises(VnicCapabilityError):
        await remove_vnic(_hmc(), "system-a", "client-a", "2")


@pytest.mark.parametrize(
    ("vnic_changes", "backing_changes"),
    [
        ({}, {"lpar_name": "vios-b"}),
        ({}, {"lpar_id": "101"}),
        ({}, {"adapter_id": "2"}),
        ({}, {"physical_port_id": "2"}),
        ({}, {"logical_port_id": "4"}),
        ({}, {"capacity": "3.0"}),
        ({}, {"desired_capacity": "3.0"}),
    ],
)
@pytest.mark.asyncio
async def test_remove_requires_full_backing_correlation(
    monkeypatch: pytest.MonkeyPatch,
    vnic_changes: dict[str, str],
    backing_changes: dict[str, str],
) -> None:
    _common(monkeypatch)
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.list_vnic_rows",
        AsyncMock(return_value=[_vnic(**vnic_changes)]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.list_vnic_backing_rows",
        AsyncMock(return_value=[_backing(**backing_changes)]),
    )

    with pytest.raises(VnicCapabilityError):
        await remove_vnic(_hmc(), "system-a", "client-a", "2")


@pytest.mark.parametrize(
    "replacement",
    [
        {"lpar_name": "vios-b"},
        {"lpar_id": "101"},
        {"physical_port_id": "2"},
        {"capacity": "3.0"},
        {"desired_capacity": "3.0"},
    ],
)
@pytest.mark.asyncio
async def test_remove_ignores_distinct_backing_reusing_adapter_and_logical_port(
    monkeypatch: pytest.MonkeyPatch, replacement: dict[str, str]
) -> None:
    _common(monkeypatch)
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.list_vnic_rows",
        AsyncMock(side_effect=[[_vnic()], []]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.list_vnic_backing_rows",
        AsyncMock(side_effect=[[_backing()], [_backing(**replacement)]]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.remove_vnic_slot",
        AsyncMock(return_value="removed"),
    )

    result = await remove_vnic(_hmc(), "system-a", "client-a", "2")

    assert result.changed is True
    assert result.backing_after == ()


@pytest.mark.asyncio
async def test_remove_successful_reads_with_changed_slot_are_contradictory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _common(monkeypatch)
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.list_vnic_rows",
        AsyncMock(side_effect=[[_vnic()], [_vnic(vlan="8")]]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.list_vnic_backing_rows",
        AsyncMock(side_effect=[[_backing()], [_backing()]]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations_ssh_network.remove_vnic_slot", AsyncMock()
    )

    with pytest.raises(VnicPartialError) as caught:
        await remove_vnic(_hmc(), "system-a", "client-a", "2")

    result = caught.value.result
    assert result.changed is None
    assert result.vnic_after_read_succeeded
    assert result.backing_after_read_succeeded
    assert result.vnic_after[0].port_vlan_id == 8
    assert len(result.backing_after) == 1
