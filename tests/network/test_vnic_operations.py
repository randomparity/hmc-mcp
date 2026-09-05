from dataclasses import fields
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from hmc_mcp.config import HMCConfig
from hmc_mcp.operations.io_virtualization.vnic import (
    VnicBackingSelector,
    VnicCapabilityError,
    VnicChangeResult,
    VnicPartialError,
    add_vnic,
    remove_vnic,
)


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
    module = "hmc_mcp.operations.io_virtualization.vnic"
    monkeypatch.setattr(
        f"{module}.resolve_and_authorize_lpar_names",
        AsyncMock(return_value=("system-a", "client-a")),
    )
    monkeypatch.setattr(f"{module}.require_admitted_environment", AsyncMock())
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
    selector = VnicBackingSelector(**values, capacity_percent=Decimal(2))
    with pytest.raises(ValueError, match="must not be blank"):
        await add_vnic(_hmc(), "system-a", "client-a", selector, 7)


@pytest.mark.parametrize(
    "capacity", [Decimal(0), Decimal(101), Decimal("NaN"), Decimal("1.001")]
)
@pytest.mark.asyncio
async def test_add_rejects_invalid_capacity(capacity: Decimal) -> None:
    selector = VnicBackingSelector("vios-a", "100", "1", "1", capacity)
    with pytest.raises(ValueError):
        await add_vnic(_hmc(), "system-a", "client-a", selector, 7)


@pytest.mark.parametrize("vlan", [-1, 4095])
@pytest.mark.asyncio
async def test_add_rejects_vlan_out_of_range(vlan: int) -> None:
    selector = VnicBackingSelector("vios-a", "100", "1", "1", Decimal(2))
    with pytest.raises(ValueError, match="between 0 and 4094"):
        await add_vnic(_hmc(), "system-a", "client-a", selector, vlan)


@pytest.mark.parametrize("vlan", [7.5, True])
@pytest.mark.asyncio
async def test_add_rejects_non_integer_vlan_before_preflight(
    monkeypatch: pytest.MonkeyPatch, vlan: object
) -> None:
    selector = VnicBackingSelector("vios-a", "100", "1", "1", Decimal(2))
    preflight = AsyncMock()
    mutation = AsyncMock()
    monkeypatch.setattr("hmc_mcp.operations.io_virtualization.vnic._preflight_add", preflight)
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.add_vnic_backing", mutation
    )

    with pytest.raises(ValueError, match="integer between 0 and 4094"):
        await add_vnic(
            object(),  # ty: ignore[invalid-argument-type]
            "system-a",
            "client-a",
            selector,
            vlan,  # ty: ignore[invalid-argument-type]
        )

    preflight.assert_not_awaited()
    mutation.assert_not_awaited()


@pytest.mark.parametrize(
    ("field", "character"),
    [
        (field, character)
        for field in ("vios_name", "vios_lpar_id", "adapter_id", "physical_port_id")
        for character in ("/", ",", "=", '"', "\n")
    ],
)
@pytest.mark.asyncio
async def test_add_rejects_each_delimiter_in_each_selector_field(
    field: str, character: str
) -> None:
    values = {
        "vios_name": "vios-a",
        "vios_lpar_id": "100",
        "adapter_id": "1",
        "physical_port_id": "1",
    }
    values[field] += character
    selector = VnicBackingSelector(**values, capacity_percent=Decimal(2))
    with pytest.raises(ValueError):
        await add_vnic(_hmc(), "system-a", "client-a", selector, 7)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("vios_name", "vios $(touch nope)"),
        ("vios_lpar_id", "100; whoami"),
        ("adapter_id", "1 & other"),
        ("physical_port_id", "1 ' port"),
    ],
)
@pytest.mark.asyncio
async def test_add_preserves_shell_metacharacters_as_quoted_payload_data(
    monkeypatch: pytest.MonkeyPatch, field: str, value: str
) -> None:
    _common(monkeypatch)
    values = {
        "vios_name": "vios-a",
        "vios_lpar_id": "100",
        "adapter_id": "1",
        "physical_port_id": "1",
    }
    values[field] = value
    selector = VnicBackingSelector(**values, capacity_percent=Decimal(2))
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.read_vios_identity",
        AsyncMock(
            return_value={
                "name": values["vios_name"],
                "lpar_id": values["vios_lpar_id"],
                "lpar_env": "vioserver",
            }
        ),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_sriov_adapter_rows",
        AsyncMock(
            return_value=[
                {
                    "adapter_id": values["adapter_id"],
                    "config_state": "sriov",
                    "functional_state": "1",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_sriov_physical_port_rows",
        AsyncMock(
            return_value=[
                {
                    "adapter_id": values["adapter_id"],
                    "phys_port_id": values["physical_port_id"],
                    "state": "1",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_rows", AsyncMock(side_effect=[[], []])
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_backing_rows",
        AsyncMock(side_effect=[[], []]),
    )
    mutate = AsyncMock()
    monkeypatch.setattr("hmc_mcp.operations.io_virtualization.vnic.add_vnic_backing", mutate)

    with pytest.raises(VnicPartialError):
        await add_vnic(_hmc(), "system-a", "client-a", selector, 7)

    mutate.assert_awaited_once()
    assert mutate.await_args_list[0].args[3] == "/".join(
        (
            "sriov",
            values["vios_name"],
            values["vios_lpar_id"],
            values["adapter_id"],
            values["physical_port_id"],
            "2",
        )
    )


@pytest.mark.parametrize(
    "identity",
    [
        {"name": "vios-b", "lpar_id": "100", "lpar_env": "vioserver"},
        {"name": "vios-a", "lpar_id": "101", "lpar_env": "vioserver"},
        {"name": "vios-a", "lpar_id": "100", "lpar_env": "aixlinux"},
    ],
)
@pytest.mark.asyncio
async def test_add_rejects_wrong_vios_identity_or_type(
    monkeypatch: pytest.MonkeyPatch, identity: dict[str, str]
) -> None:
    _common(monkeypatch)
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.read_vios_identity",
        AsyncMock(return_value=identity),
    )
    with pytest.raises(VnicCapabilityError, match="name, ID, or partition type"):
        await add_vnic(
            _hmc(),
            "system-a",
            "client-a",
            VnicBackingSelector("vios-a", "100", "1", "1", Decimal(2)),
            7,
        )


@pytest.mark.parametrize(
    "inventory",
    [
        "missing-adapter",
        "wrong-adapter",
        "degraded-adapter",
        "missing-port",
        "wrong-port",
        "inactive-port",
    ],
)
@pytest.mark.asyncio
async def test_add_rejects_adapter_or_port_mismatch(
    monkeypatch: pytest.MonkeyPatch, inventory: str
) -> None:
    _common(monkeypatch)
    if "adapter" in inventory:
        rows = (
            []
            if inventory == "missing-adapter"
            else [
                {
                    "adapter_id": "2" if inventory == "wrong-adapter" else "1",
                    "config_state": "sriov",
                    "functional_state": "0" if inventory == "degraded-adapter" else "1",
                }
            ]
        )
        monkeypatch.setattr(
            "hmc_mcp.operations.io_virtualization.vnic.list_sriov_adapter_rows",
            AsyncMock(return_value=rows),
        )
    else:
        rows = (
            []
            if inventory == "missing-port"
            else [
                {
                    "adapter_id": "1",
                    "phys_port_id": "2" if inventory == "wrong-port" else "1",
                    "state": "0" if inventory == "inactive-port" else "1",
                }
            ]
        )
        monkeypatch.setattr(
            "hmc_mcp.operations.io_virtualization.vnic.list_sriov_physical_port_rows",
            AsyncMock(return_value=rows),
        )
    with pytest.raises(VnicCapabilityError):
        await add_vnic(
            _hmc(),
            "system-a",
            "client-a",
            VnicBackingSelector("vios-a", "100", "1", "1", Decimal(2)),
            7,
        )


@pytest.mark.asyncio
async def test_add_rejects_exhausted_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    _common(monkeypatch)
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_sriov_configured_logical_port_rows",
        AsyncMock(
            return_value=[
                {
                    "adapter_id": "1",
                    "logical_port_id": "9",
                    "phys_port_id": "1",
                    "capacity": "99",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_rows", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_backing_rows",
        AsyncMock(return_value=[]),
    )
    with pytest.raises(ValueError, match="capacity exhausted"):
        await add_vnic(
            _hmc(),
            "system-a",
            "client-a",
            VnicBackingSelector("vios-a", "100", "1", "1", Decimal(2)),
            7,
        )


@pytest.mark.asyncio
async def test_add_verified_retry_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    _common(monkeypatch)
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_rows",
        AsyncMock(return_value=[_vnic()]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_backing_rows",
        AsyncMock(return_value=[_backing()]),
    )
    mutate = AsyncMock()
    monkeypatch.setattr("hmc_mcp.operations.io_virtualization.vnic.add_vnic_backing", mutate)
    result = await add_vnic(
        _hmc(),
        "system-a",
        "client-a",
        VnicBackingSelector("vios-a", "100", "1", "1", Decimal(2)),
        7,
    )
    assert (result.changed, result.slot_num, result.mutation_dispatched) == (
        False,
        "2",
        False,
    )
    mutate.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_verified_retry_resolves_before_new_allocation_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _common(monkeypatch)
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_sriov_configured_logical_port_rows",
        AsyncMock(
            return_value=[
                {
                    "adapter_id": "1",
                    "logical_port_id": "3",
                    "phys_port_id": "1",
                    "capacity": "60",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_rows",
        AsyncMock(return_value=[_vnic(capacity="60", desired_capacity="60")]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_backing_rows",
        AsyncMock(return_value=[_backing(capacity="60", desired_capacity="60")]),
    )
    mutate = AsyncMock()
    monkeypatch.setattr("hmc_mcp.operations.io_virtualization.vnic.add_vnic_backing", mutate)

    result = await add_vnic(
        _hmc(),
        "system-a",
        "client-a",
        VnicBackingSelector("vios-a", "100", "1", "1", Decimal(60)),
        7,
    )

    assert (result.changed, result.slot_num, result.mutation_dispatched) == (
        False,
        "2",
        False,
    )
    mutate.assert_not_awaited()


@pytest.mark.parametrize("projection", ["direct", "backing"])
@pytest.mark.asyncio
async def test_add_rejects_identical_duplicates_within_one_projection(
    monkeypatch: pytest.MonkeyPatch, projection: str
) -> None:
    _common(monkeypatch)
    direct_row = {
        "adapter_id": "1",
        "logical_port_id": "3",
        "phys_port_id": "1",
        "capacity": "2.0",
    }
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_sriov_configured_logical_port_rows",
        AsyncMock(
            return_value=[direct_row, direct_row] if projection == "direct" else []
        ),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_rows", AsyncMock(return_value=[])
    )
    backing_rows = [_backing(), _backing()] if projection == "backing" else []
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_backing_rows",
        AsyncMock(return_value=backing_rows),
    )
    mutate = AsyncMock()
    monkeypatch.setattr("hmc_mcp.operations.io_virtualization.vnic.add_vnic_backing", mutate)

    with pytest.raises(ValueError, match=f"duplicate {projection}"):
        await add_vnic(
            _hmc(),
            "system-a",
            "client-a",
            VnicBackingSelector("vios-a", "100", "1", "1", Decimal(2)),
            7,
        )

    mutate.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_deduplicates_consistent_direct_and_backing_observations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _common(monkeypatch)
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_sriov_configured_logical_port_rows",
        AsyncMock(
            return_value=[
                {
                    "adapter_id": "1",
                    "logical_port_id": "3",
                    "phys_port_id": "1",
                    "capacity": "98.0",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_rows", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_backing_rows",
        AsyncMock(return_value=[_backing(capacity="50", desired_capacity="98.0")]),
    )
    mutate = AsyncMock(side_effect=RuntimeError("dispatched"))
    monkeypatch.setattr("hmc_mcp.operations.io_virtualization.vnic.add_vnic_backing", mutate)

    with pytest.raises(VnicPartialError):
        await add_vnic(
            _hmc(),
            "system-a",
            "client-a",
            VnicBackingSelector("vios-a", "100", "1", "1", Decimal(2)),
            7,
        )

    mutate.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_successfully_correlates_new_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _common(monkeypatch)
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_rows",
        AsyncMock(side_effect=[[], [_vnic()]]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_backing_rows",
        AsyncMock(side_effect=[[], [_backing()]]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.add_vnic_backing",
        AsyncMock(return_value="created"),
    )
    result = await add_vnic(
        _hmc(),
        "system-a",
        "client-a",
        VnicBackingSelector("vios-a", "100", "1", "1", Decimal(2)),
        7,
    )
    assert (result.changed, result.slot_num, result.output) == (True, "2", "created")


@pytest.mark.asyncio
async def test_add_ignores_unrelated_equal_selector_backing_for_target_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _common(monkeypatch)
    unrelated = _backing(logical="9")
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_rows",
        AsyncMock(side_effect=[[], [_vnic()]]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_backing_rows",
        AsyncMock(side_effect=[[unrelated], [unrelated, _backing()]]),
    )
    mutation = AsyncMock(return_value="created")
    monkeypatch.setattr("hmc_mcp.operations.io_virtualization.vnic.add_vnic_backing", mutation)

    result = await add_vnic(
        _hmc(),
        "system-a",
        "client-a",
        VnicBackingSelector("vios-a", "100", "1", "1", Decimal(2)),
        7,
    )

    assert result.changed is True
    assert result.backing_before == ()
    assert tuple(item.logical_port_id for item in result.backing_after) == ("3",)
    mutation.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_before_state_after_dispatch_is_known_unchanged_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _common(monkeypatch)
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_rows",
        AsyncMock(side_effect=[[], []]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_backing_rows",
        AsyncMock(side_effect=[[], []]),
    )
    monkeypatch.setattr("hmc_mcp.operations.io_virtualization.vnic.add_vnic_backing", AsyncMock())

    with pytest.raises(VnicPartialError) as caught:
        await add_vnic(
            _hmc(),
            "system-a",
            "client-a",
            VnicBackingSelector("vios-a", "100", "1", "1", Decimal(2)),
            7,
        )

    result = caught.value.result
    assert result.mutation_dispatched
    assert result.changed is False
    assert result.slot_num is None
    assert result.vnic_after == ()
    assert result.backing_after == ()


@pytest.mark.parametrize(
    ("case", "vnic_after", "backing_after", "mutation_error", "expected_changed"),
    [
        ("final-success", [_vnic()], [_backing()], None, True),
        ("final-command-error", [_vnic()], [_backing()], RuntimeError("command"), True),
        ("before", [], [], None, False),
        ("contradictory", [_vnic()], [], None, None),
        ("one-read", [_vnic()], TimeoutError("backing timeout"), None, None),
        (
            "neither-read",
            TimeoutError("vnic timeout"),
            TimeoutError("backing timeout"),
            None,
            None,
        ),
    ],
)
@pytest.mark.asyncio
async def test_add_reconciliation_decision_table(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    vnic_after: list[dict[str, str]] | Exception,
    backing_after: list[dict[str, str]] | Exception,
    mutation_error: Exception | None,
    expected_changed: bool | None,
) -> None:
    _common(monkeypatch)
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_rows",
        AsyncMock(side_effect=[[], vnic_after]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_backing_rows",
        AsyncMock(side_effect=[[], backing_after]),
    )
    mutation = AsyncMock(return_value="created")
    if mutation_error is not None:
        mutation.side_effect = mutation_error
    monkeypatch.setattr("hmc_mcp.operations.io_virtualization.vnic.add_vnic_backing", mutation)
    call = add_vnic(
        _hmc(),
        "system-a",
        "client-a",
        VnicBackingSelector("vios-a", "100", "1", "1", Decimal(2)),
        7,
    )

    if case == "final-success":
        result = await call
    else:
        with pytest.raises(VnicPartialError) as caught:
            await call
        result = caught.value.result
        expected_cause = mutation_error
        if expected_cause is None and isinstance(vnic_after, Exception):
            expected_cause = vnic_after
        if expected_cause is None and isinstance(backing_after, Exception):
            expected_cause = backing_after
        assert caught.value.__cause__ is expected_cause

    assert result.changed is expected_changed
    assert result.vnic_after_read_succeeded is not isinstance(vnic_after, Exception)
    assert result.backing_after_read_succeeded is not isinstance(
        backing_after, Exception
    )


@pytest.mark.asyncio
async def test_add_retry_ignores_unrelated_selector_matching_degraded_backing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _common(monkeypatch)
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_rows",
        AsyncMock(return_value=[_vnic()]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_backing_rows",
        AsyncMock(return_value=[_backing(), _backing(logical="4", is_active="0")]),
    )
    mutate = AsyncMock()
    monkeypatch.setattr("hmc_mcp.operations.io_virtualization.vnic.add_vnic_backing", mutate)

    result = await add_vnic(
        _hmc(),
        "system-a",
        "client-a",
        VnicBackingSelector("vios-a", "100", "1", "1", Decimal(2)),
        7,
    )

    assert result.changed is False
    assert tuple(item.logical_port_id for item in result.backing_before) == ("3",)
    mutate.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_final_ignores_unrelated_selector_matching_degraded_backing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _common(monkeypatch)
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_rows",
        AsyncMock(side_effect=[[], [_vnic()]]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_backing_rows",
        AsyncMock(
            side_effect=[[], [_backing(), _backing(logical="4", status="Degraded")]]
        ),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.add_vnic_backing",
        AsyncMock(return_value="created"),
    )

    result = await add_vnic(
        _hmc(),
        "system-a",
        "client-a",
        VnicBackingSelector("vios-a", "100", "1", "1", Decimal(2)),
        7,
    )

    assert result.changed is True
    assert result.slot_num == "2"
    assert tuple(item.logical_port_id for item in result.backing_after) == ("3",)


@pytest.mark.asyncio
async def test_add_retry_refuses_degraded_correlated_target_backing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _common(monkeypatch)
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_rows",
        AsyncMock(return_value=[_vnic()]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_backing_rows",
        AsyncMock(return_value=[_backing(status="Degraded")]),
    )
    mutation = AsyncMock()
    monkeypatch.setattr("hmc_mcp.operations.io_virtualization.vnic.add_vnic_backing", mutation)

    with pytest.raises(VnicCapabilityError, match="ambiguous or degraded"):
        await add_vnic(
            _hmc(),
            "system-a",
            "client-a",
            VnicBackingSelector("vios-a", "100", "1", "1", Decimal(2)),
            7,
        )

    mutation.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_rejects_two_new_matching_vnics_despite_one_operational_backing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _common(monkeypatch)
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_rows",
        AsyncMock(side_effect=[[], [_vnic(), _vnic(slot="3", logical="4")]]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_backing_rows",
        AsyncMock(side_effect=[[], [_backing()]]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.add_vnic_backing",
        AsyncMock(return_value="created"),
    )

    with pytest.raises(VnicPartialError) as caught:
        await add_vnic(
            _hmc(),
            "system-a",
            "client-a",
            VnicBackingSelector("vios-a", "100", "1", "1", Decimal(2)),
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
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_rows",
        AsyncMock(side_effect=[[], [_vnic()]]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_backing_rows",
        AsyncMock(side_effect=[[], []]),
    )
    monkeypatch.setattr("hmc_mcp.operations.io_virtualization.vnic.add_vnic_backing", AsyncMock())

    with pytest.raises(VnicPartialError) as caught:
        await add_vnic(
            _hmc(),
            "system-a",
            "client-a",
            VnicBackingSelector("vios-a", "100", "1", "1", Decimal(2)),
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
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_rows",
        AsyncMock(side_effect=[[], TimeoutError("vnic read")]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_backing_rows",
        AsyncMock(side_effect=[[], OSError("backing read")]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.add_vnic_backing",
        AsyncMock(side_effect=RuntimeError("HMC rejected VLAN")),
    )
    with pytest.raises(VnicPartialError) as caught:
        await add_vnic(
            _hmc(),
            "system-a",
            "client-a",
            VnicBackingSelector("vios-a", "100", "1", "1", Decimal(2)),
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
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_rows", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_backing_rows",
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
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_rows",
        AsyncMock(side_effect=[[_vnic()], []]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_backing_rows",
        AsyncMock(side_effect=[[_backing()], []]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.remove_vnic_slot",
        AsyncMock(return_value="removed"),
    )
    result = await remove_vnic(_hmc(), "system-a", "client-a", "2")
    assert result.changed is True
    assert result.selector == VnicBackingSelector(
        "vios-a", "100", "1", "1", Decimal("2.0")
    )


@pytest.mark.parametrize(
    ("case", "vnic_after", "backing_after", "mutation_error", "expected_changed"),
    [
        ("final-success", [], [], None, True),
        ("final-command-error", [], [], RuntimeError("command"), True),
        ("before", [_vnic()], [_backing()], None, False),
        ("contradictory", [], [_backing()], None, None),
        ("one-read", [], TimeoutError("backing timeout"), None, None),
        (
            "neither-read",
            TimeoutError("vnic timeout"),
            TimeoutError("backing timeout"),
            None,
            None,
        ),
    ],
)
@pytest.mark.asyncio
async def test_remove_reconciliation_decision_table(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    vnic_after: list[dict[str, str]] | Exception,
    backing_after: list[dict[str, str]] | Exception,
    mutation_error: Exception | None,
    expected_changed: bool | None,
) -> None:
    _common(monkeypatch)
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_rows",
        AsyncMock(side_effect=[[_vnic()], vnic_after]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_backing_rows",
        AsyncMock(side_effect=[[_backing()], backing_after]),
    )
    mutation = AsyncMock(return_value="removed")
    if mutation_error is not None:
        mutation.side_effect = mutation_error
    monkeypatch.setattr("hmc_mcp.operations.io_virtualization.vnic.remove_vnic_slot", mutation)
    call = remove_vnic(_hmc(), "system-a", "client-a", "2")

    if case == "final-success":
        result = await call
    else:
        with pytest.raises(VnicPartialError) as caught:
            await call
        result = caught.value.result

    assert result.changed is expected_changed
    assert result.vnic_after_read_succeeded is not isinstance(vnic_after, Exception)
    assert result.backing_after_read_succeeded is not isinstance(
        backing_after, Exception
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
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_rows",
        AsyncMock(return_value=[_vnic()]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_backing_rows",
        AsyncMock(return_value=rows),
    )
    with pytest.raises(VnicCapabilityError):
        await remove_vnic(_hmc(), "system-a", "client-a", "2")


@pytest.mark.parametrize("backing_devices", ["none", None, "extra"])
@pytest.mark.asyncio
async def test_remove_requires_exactly_one_embedded_backing_before_mutation(
    monkeypatch: pytest.MonkeyPatch, backing_devices: str | None
) -> None:
    _common(monkeypatch)
    row = _vnic()
    if backing_devices is None:
        row["backing_devices"] = ""
    elif backing_devices == "extra":
        row["backing_devices"] = f"{row['backing_devices']},{row['backing_devices']}"
    else:
        row["backing_devices"] = backing_devices
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_rows", AsyncMock(return_value=[row])
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_backing_rows",
        AsyncMock(return_value=[_backing()]),
    )
    mutate = AsyncMock()
    monkeypatch.setattr("hmc_mcp.operations.io_virtualization.vnic.remove_vnic_slot", mutate)

    with pytest.raises(VnicCapabilityError, match="exactly one embedded backing"):
        await remove_vnic(_hmc(), "system-a", "client-a", "2")

    mutate.assert_not_awaited()


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
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_rows",
        AsyncMock(return_value=[_vnic(**vnic_changes)]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_backing_rows",
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
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_rows",
        AsyncMock(side_effect=[[_vnic()], []]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_backing_rows",
        AsyncMock(side_effect=[[_backing()], [_backing(**replacement)]]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.remove_vnic_slot",
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
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_rows",
        AsyncMock(side_effect=[[_vnic()], [_vnic(vlan="8")]]),
    )
    monkeypatch.setattr(
        "hmc_mcp.operations.io_virtualization.vnic.list_vnic_backing_rows",
        AsyncMock(side_effect=[[_backing()], [_backing()]]),
    )
    monkeypatch.setattr("hmc_mcp.operations.io_virtualization.vnic.remove_vnic_slot", AsyncMock())

    with pytest.raises(VnicPartialError) as caught:
        await remove_vnic(_hmc(), "system-a", "client-a", "2")

    result = caught.value.result
    assert result.changed is None
    assert result.vnic_after_read_succeeded
    assert result.backing_after_read_succeeded
    assert result.vnic_after[0].port_vlan_id == 8
    assert len(result.backing_after) == 1
