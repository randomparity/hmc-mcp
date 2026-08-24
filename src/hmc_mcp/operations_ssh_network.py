"""Presentation-neutral, verified SSH network workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Literal, cast

from hmc_mcp.client import HMCClient
from hmc_mcp.common import resolve_lpar_uuid, resolve_system_uuid
from hmc_mcp.config import HMCConfig
from hmc_mcp.operations_lpar import (
    authorize_lpar_mutation,
    resolve_lpar_ownership_names,
)
from hmc_mcp.operations_pcie import _require_admitted_environment
from hmc_mcp.ssh_commands import (
    add_vnic_backing,
    get_lpar_memopt_score as _get_lpar_memopt_score,
    list_fc_ports as _list_fc_ports,
    list_lpar_memopt_scores as _list_lpar_memopt_scores,
    list_sea_adapters as _list_sea_adapters,
    list_sriov_adapter_rows,
    list_sriov_configured_logical_port_rows,
    list_sriov_physical_port_rows,
    list_vnic_backing_rows,
    list_vnic_rows,
    list_vnics as _list_vnics,
    read_vios_identity,
    remove_vnic_slot,
)
from hmc_mcp.ssh_selectors import resolve_ssh_names


@dataclass(frozen=True)
class VnicBackingSelector:
    vios_name: str = field(metadata={"description": "VIOS partition name."})
    vios_lpar_id: str = field(metadata={"description": "VIOS partition ID."})
    adapter_id: str = field(metadata={"description": "SR-IOV adapter identifier."})
    physical_port_id: str = field(metadata={"description": "Physical-port identifier."})
    capacity_percent: Decimal = field(
        metadata={"description": "Requested decimal capacity percentage."}
    )


@dataclass(frozen=True)
class VnicBackingSnapshot:
    vios_name: str
    vios_lpar_id: str
    adapter_id: str
    physical_port_id: str
    logical_port_id: str
    capacity_percent: Decimal
    desired_capacity_percent: Decimal
    maximum_capacity_percent: Decimal
    desired_maximum_capacity_percent: Decimal
    failover_priority: str
    is_active: bool
    status: str


@dataclass(frozen=True)
class VnicSnapshot:
    lpar_name: str
    lpar_id: str
    slot_num: str
    port_vlan_id: int
    backing_devices: tuple[VnicBackingSnapshot, ...]


@dataclass(frozen=True)
class VnicChangeResult:
    operation: Literal["add", "remove"]
    mutation_dispatched: bool
    changed: bool | None
    selector: VnicBackingSelector | None
    slot_num: str | None
    vnic_before: tuple[VnicSnapshot, ...]
    backing_before: tuple[VnicBackingSnapshot, ...]
    vnic_after: tuple[VnicSnapshot, ...]
    backing_after: tuple[VnicBackingSnapshot, ...]
    vnic_after_read_succeeded: bool
    backing_after_read_succeeded: bool
    output: str
    errors: tuple[str, ...]


class VnicCapabilityError(RuntimeError):
    """Raised when evidence does not admit a requested mutation."""


class VnicPartialError(RuntimeError):
    """Raised when a dispatched mutation cannot be fully reconciled."""

    def __init__(self, message: str, result: VnicChangeResult):
        super().__init__(message)
        self.result = result


async def list_fc_ports(
    config: HMCConfig, system: str, lpar: str | None = None
) -> list[dict[str, str]]:
    system_name, lpar_name = await resolve_ssh_names(config, system, lpar)
    return await _list_fc_ports(config, cast(str, system_name), lpar_name)


async def list_sea_adapters(
    config: HMCConfig, system: str, lpar: str | None = None
) -> list[dict[str, str]]:
    system_name, lpar_name = await resolve_ssh_names(config, system, lpar)
    return await _list_sea_adapters(config, cast(str, system_name), lpar_name)


async def list_vnics(
    config: HMCConfig, system: str, lpar: str
) -> list[dict[str, object]]:
    system_name, lpar_name = await resolve_ssh_names(config, system, lpar)
    return await _list_vnics(config, cast(str, system_name), cast(str, lpar_name))


async def get_lpar_memopt_score(
    config: HMCConfig, system: str, lpar: str
) -> dict[str, object]:
    """Return one LPAR's current memory-optimization score."""
    system_name, lpar_name = await resolve_ssh_names(config, system, lpar)
    return await _get_lpar_memopt_score(
        config, cast(str, system_name), cast(str, lpar_name)
    )


async def list_lpar_memopt_scores(
    config: HMCConfig, system: str, lpar: str | None = None
) -> list[dict[str, object]]:
    """Return current memory-optimization scores for selected system LPARs."""
    system_name, lpar_name = await resolve_ssh_names(config, system, lpar)
    return await _list_lpar_memopt_scores(config, cast(str, system_name), lpar_name)


def _required(value: str, name: str) -> str:
    if not value.strip():
        raise ValueError(f"{name} must not be blank")
    structural = {"/": "slash", ",": "comma", "=": "equals sign", '"': "double quote"}
    for character, label in structural.items():
        if character in value:
            raise ValueError(
                f"{name} contains {label}; it would alter HMC command structure"
            )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{name} contains a control character")
    return value


def _capacity(value: Decimal) -> Decimal:
    if not value.is_finite() or value < 1 or value > 100:
        raise ValueError("capacity_percent must be between 1 and 100")
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -2:
        raise ValueError("capacity_percent supports at most two decimal places")
    return value


def _decimal(value: str, field: str) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"{field} must be a decimal percentage") from error
    if not result.is_finite():
        raise ValueError(f"{field} must be a finite decimal percentage")
    return result


def _validated(selector: VnicBackingSelector) -> VnicBackingSelector:
    return VnicBackingSelector(
        _required(selector.vios_name, "vios_name"),
        _required(selector.vios_lpar_id, "vios_lpar_id"),
        _required(selector.adapter_id, "adapter_id"),
        _required(selector.physical_port_id, "physical_port_id"),
        _capacity(selector.capacity_percent),
    )


def _backing(row: dict[str, str]) -> VnicBackingSnapshot:
    if row["type"] != "sriov":
        raise ValueError("vNIC backing row type must be sriov")
    return VnicBackingSnapshot(
        row["lpar_name"],
        row["lpar_id"],
        row["adapter_id"],
        row["physical_port_id"],
        row["logical_port_id"],
        _decimal(row["capacity"], "capacity"),
        _decimal(row["desired_capacity"], "desired_capacity"),
        _decimal(row["max_capacity"], "max_capacity"),
        _decimal(row["desired_max_capacity"], "desired_max_capacity"),
        row["failover_priority"],
        row["is_active"] == "1",
        row["status"],
    )


def _embedded(value: str) -> tuple[VnicBackingSnapshot, ...]:
    if not value.strip() or value == "none":
        return ()
    result: list[VnicBackingSnapshot] = []
    for record in value.split(","):
        parts = record.split("/")
        if len(parts) != 11 or parts[0] != "sriov":
            raise ValueError("vNIC backing_devices row has an unsupported shape")
        result.append(
            VnicBackingSnapshot(
                parts[1],
                parts[2],
                parts[3],
                parts[4],
                parts[5],
                _decimal(parts[6], "capacity"),
                _decimal(parts[7], "desired_capacity"),
                _decimal(parts[9], "max_capacity"),
                _decimal(parts[10], "desired_max_capacity"),
                parts[8],
                False,
                "",
            )
        )
    return tuple(result)


def _vnics(rows: list[dict[str, str]]) -> tuple[VnicSnapshot, ...]:
    result = tuple(
        VnicSnapshot(
            row["lpar_name"],
            row["lpar_id"],
            row["slot_num"],
            int(row["port_vlan_id"]),
            _embedded(row["backing_devices"]),
        )
        for row in rows
    )
    slots = [item.slot_num for item in result]
    if len(slots) != len(set(slots)):
        raise ValueError("duplicate vNIC slot inventory rows")
    return result


async def _resolve(
    hmc: HMCClient, system: str, lpar: str, override: bool
) -> tuple[str, str]:
    system_uuid = await resolve_system_uuid(hmc, system)
    lpar_uuid = await resolve_lpar_uuid(hmc, lpar, system_name_or_uuid=system_uuid)
    names = await resolve_lpar_ownership_names(hmc, system_uuid, system, lpar_uuid)
    await authorize_lpar_mutation(hmc, *names, ownership_override=override)
    return names


def _matches(item: VnicBackingSnapshot, selector: VnicBackingSelector) -> bool:
    return (
        item.vios_name,
        item.vios_lpar_id,
        item.adapter_id,
        item.physical_port_id,
        item.desired_capacity_percent,
    ) == (
        selector.vios_name,
        selector.vios_lpar_id,
        selector.adapter_id,
        selector.physical_port_id,
        selector.capacity_percent,
    )


def _correlated(vnic: VnicSnapshot, backing: VnicBackingSnapshot) -> bool:
    return any(_same_backing_identity(item, backing) for item in vnic.backing_devices)


def _same_backing_identity(
    left: VnicBackingSnapshot, right: VnicBackingSnapshot
) -> bool:
    return (
        left.vios_name,
        left.vios_lpar_id,
        left.adapter_id,
        left.physical_port_id,
        left.logical_port_id,
        left.capacity_percent,
        left.desired_capacity_percent,
    ) == (
        right.vios_name,
        right.vios_lpar_id,
        right.adapter_id,
        right.physical_port_id,
        right.logical_port_id,
        right.capacity_percent,
        right.desired_capacity_percent,
    )


def _pairs(
    vnics: tuple[VnicSnapshot, ...],
    backings: tuple[VnicBackingSnapshot, ...],
    selector: VnicBackingSelector,
    vlan: int,
) -> list[tuple[VnicSnapshot, VnicBackingSnapshot]]:
    return [
        (vnic, backing)
        for vnic in vnics
        if vnic.port_vlan_id == vlan
        for backing in backings
        if _matches(backing, selector)
        and _correlated(vnic, backing)
        and backing.is_active
        and backing.status == "Operational"
    ]


def _matching_vnics(
    vnics: tuple[VnicSnapshot, ...], selector: VnicBackingSelector, vlan: int
) -> tuple[VnicSnapshot, ...]:
    return tuple(
        vnic
        for vnic in vnics
        if vnic.port_vlan_id == vlan
        and any(_matches(item, selector) for item in vnic.backing_devices)
    )


def _correlated_matching_backings(
    vnics: tuple[VnicSnapshot, ...],
    backings: tuple[VnicBackingSnapshot, ...],
    selector: VnicBackingSelector,
) -> tuple[VnicBackingSnapshot, ...]:
    return tuple(
        item
        for item in backings
        if _matches(item, selector) and any(_correlated(vnic, item) for vnic in vnics)
    )


async def _preflight_add(
    hmc: HMCClient,
    system: str,
    lpar: str,
    selector: VnicBackingSelector,
    override: bool,
) -> tuple[
    str,
    str,
    tuple[VnicSnapshot, ...],
    tuple[VnicBackingSnapshot, ...],
    Decimal,
]:
    system_name, lpar_name = await _resolve(hmc, system, lpar, override)
    config = hmc.config
    await _require_admitted_environment(config, system_name)
    identity = await read_vios_identity(config, system_name, selector.vios_name)
    expected = {
        "name": selector.vios_name,
        "lpar_id": selector.vios_lpar_id,
        "lpar_env": "vioserver",
    }
    if identity != expected:
        raise VnicCapabilityError(
            "selected VIOS name, ID, or partition type does not match inventory"
        )
    adapters = [
        row
        for row in await list_sriov_adapter_rows(config, system_name)
        if row["adapter_id"] == selector.adapter_id
    ]
    if (
        len(adapters) != 1
        or adapters[0]["config_state"] != "sriov"
        or adapters[0]["functional_state"] != "1"
    ):
        raise VnicCapabilityError("adapter is not in healthy SR-IOV mode")
    ports = [
        row
        for row in await list_sriov_physical_port_rows(
            config, system_name, selector.adapter_id
        )
        if row["phys_port_id"] == selector.physical_port_id
    ]
    if len(ports) != 1 or ports[0]["state"] != "1":
        raise VnicCapabilityError("physical port is unavailable or mismatched")
    direct = await list_sriov_configured_logical_port_rows(
        config, system_name, selector.adapter_id
    )
    before = _vnics(await list_vnic_rows(config, system_name, lpar_name))
    backings = tuple(
        _backing(row) for row in await list_vnic_backing_rows(config, system_name)
    )
    direct_observations: dict[tuple[str, str], tuple[str, Decimal]] = {}
    for row in direct:
        key = row["adapter_id"], row["logical_port_id"]
        observation = row["phys_port_id"], _decimal(row["capacity"], "capacity")
        if key in direct_observations:
            raise ValueError("duplicate direct logical-port inventory")
        direct_observations[key] = observation
    backing_observations: dict[tuple[str, str], tuple[str, Decimal]] = {}
    for item in backings:
        key = item.adapter_id, item.logical_port_id
        observation = item.physical_port_id, item.desired_capacity_percent
        if key in backing_observations:
            raise ValueError("duplicate backing logical-port inventory")
        backing_observations[key] = observation
        if key in direct_observations and direct_observations[key] != observation:
            raise ValueError("conflicting cross-projection logical-port capacity")
    used = direct_observations | backing_observations
    total = sum(
        (
            capacity
            for (adapter, _), (port, capacity) in used.items()
            if adapter == selector.adapter_id and port == selector.physical_port_id
        ),
        Decimal(),
    )
    return system_name, lpar_name, before, backings, total


async def _after(
    config: HMCConfig, system: str, lpar: str
) -> tuple[
    tuple[VnicSnapshot, ...], tuple[VnicBackingSnapshot, ...], bool, bool, list[str]
]:
    vnics: tuple[VnicSnapshot, ...] = ()
    backings: tuple[VnicBackingSnapshot, ...] = ()
    v_ok = b_ok = False
    errors: list[str] = []
    try:
        vnics = _vnics(await list_vnic_rows(config, system, lpar))
        v_ok = True
    except Exception as error:
        errors.append(f"vNIC reconciliation read failed: {error}")
    try:
        backings = tuple(
            _backing(row) for row in await list_vnic_backing_rows(config, system)
        )
        b_ok = True
    except Exception as error:
        errors.append(f"backing reconciliation read failed: {error}")
    return vnics, backings, v_ok, b_ok, errors


def _add_payload(selector: VnicBackingSelector) -> str:
    return "/".join(
        (
            "sriov",
            selector.vios_name,
            selector.vios_lpar_id,
            selector.adapter_id,
            selector.physical_port_id,
            str(selector.capacity_percent),
        )
    )


async def add_vnic(
    hmc: HMCClient,
    system: str,
    lpar: str,
    selector: VnicBackingSelector,
    port_vlan_id: int,
    *,
    ownership_override: bool = False,
) -> VnicChangeResult:
    selector = _validated(selector)
    if type(port_vlan_id) is not int or not 0 <= port_vlan_id <= 4094:
        raise ValueError("port_vlan_id must be an integer between 0 and 4094")
    (
        system_name,
        lpar_name,
        before,
        all_backing_before,
        used_capacity,
    ) = await _preflight_add(hmc, system, lpar, selector, ownership_override)
    candidates = _matching_vnics(before, selector, port_vlan_id)
    matching_backing_before = _correlated_matching_backings(
        candidates, all_backing_before, selector
    )
    pairs = _pairs(candidates, matching_backing_before, selector, port_vlan_id)
    if len(pairs) == 1 and len(candidates) == 1 and len(matching_backing_before) == 1:
        return VnicChangeResult(
            "add",
            False,
            False,
            selector,
            pairs[0][0].slot_num,
            candidates,
            matching_backing_before,
            candidates,
            matching_backing_before,
            True,
            True,
            "",
            (),
        )
    if pairs or candidates or matching_backing_before:
        raise VnicCapabilityError(
            "existing matching vNIC inventory is ambiguous or degraded"
        )
    if used_capacity + selector.capacity_percent > 100:
        raise ValueError(f"capacity exhausted: {used_capacity}% used of 100%")
    payload = _add_payload(selector)
    output = ""
    errors: list[str] = []
    try:
        output = await add_vnic_backing(
            hmc.config, system_name, lpar_name, payload, port_vlan_id
        )
    except Exception as error:
        errors.append(f"mutation failed: {error}")
    after, backing_after, v_ok, b_ok, read_errors = await _after(
        hmc.config, system_name, lpar_name
    )
    errors.extend(read_errors)
    before_slots = {item.slot_num for item in before}
    new_pairs = (
        [
            pair
            for pair in _pairs(after, backing_after, selector, port_vlan_id)
            if pair[0].slot_num not in before_slots
        ]
        if v_ok and b_ok
        else []
    )
    observed_new = (
        [
            item
            for item in _matching_vnics(after, selector, port_vlan_id)
            if item.slot_num not in before_slots
        ]
        if v_ok
        else []
    )
    slot = observed_new[0].slot_num if len(observed_new) == 1 else None
    matching_after = _matching_vnics(after, selector, port_vlan_id) if v_ok else ()
    matching_backing_after = (
        _correlated_matching_backings(matching_after, backing_after, selector)
        if b_ok and v_ok
        else ()
    )
    final = (
        v_ok
        and b_ok
        and len(observed_new) == 1
        and len(new_pairs) == 1
        and len(matching_backing_after) == 1
    )
    unchanged = (
        v_ok
        and b_ok
        and matching_after == candidates
        and matching_backing_after == matching_backing_before
    )
    changed: bool | None = True if final else False if unchanged else None
    if not final:
        errors.append(
            "add reconciliation did not prove exactly one new active Operational backing"
        )
    result = VnicChangeResult(
        "add",
        True,
        changed,
        selector,
        slot,
        candidates,
        matching_backing_before,
        matching_after,
        matching_backing_after,
        v_ok,
        b_ok,
        output,
        tuple(errors),
    )
    if errors:
        raise VnicPartialError("vNIC add could not be fully verified", result)
    return result


async def remove_vnic(
    hmc: HMCClient,
    system: str,
    lpar: str,
    slot_num: str,
    *,
    ownership_override: bool = False,
) -> VnicChangeResult:
    slot_num = _required(slot_num, "slot_num")
    system_name, lpar_name = await _resolve(hmc, system, lpar, ownership_override)
    await _require_admitted_environment(hmc.config, system_name)
    all_vnics = _vnics(await list_vnic_rows(hmc.config, system_name, lpar_name))
    all_backings = tuple(
        _backing(row) for row in await list_vnic_backing_rows(hmc.config, system_name)
    )
    selected = tuple(item for item in all_vnics if item.slot_num == slot_num)
    if not selected:
        return VnicChangeResult(
            "remove", False, False, None, slot_num, (), (), (), (), True, True, "", ()
        )
    if len(selected[0].backing_devices) != 1:
        raise VnicCapabilityError(
            "selected vNIC does not have exactly one embedded backing"
        )
    correlated = tuple(item for item in all_backings if _correlated(selected[0], item))
    if (
        len(correlated) != 1
        or not correlated[0].is_active
        or correlated[0].status != "Operational"
    ):
        raise VnicCapabilityError(
            "selected vNIC does not have exactly one active Operational backing"
        )
    captured = correlated[0]
    selector = VnicBackingSelector(
        captured.vios_name,
        captured.vios_lpar_id,
        captured.adapter_id,
        captured.physical_port_id,
        captured.desired_capacity_percent,
    )
    output = ""
    errors: list[str] = []
    try:
        output = await remove_vnic_slot(hmc.config, system_name, lpar_name, slot_num)
    except Exception as error:
        errors.append(f"mutation failed: {error}")
    after, backing_after, v_ok, b_ok, read_errors = await _after(
        hmc.config, system_name, lpar_name
    )
    errors.extend(read_errors)
    slot_absent = v_ok and not any(item.slot_num == slot_num for item in after)
    backing_absent = b_ok and not any(
        _same_backing_identity(item, captured) for item in backing_after
    )
    final = slot_absent and backing_absent
    unchanged = (
        v_ok
        and b_ok
        and selected[0] in after
        and any(_same_backing_identity(item, captured) for item in backing_after)
    )
    changed: bool | None = True if final else False if unchanged else None
    if not final:
        errors.append(
            "remove reconciliation did not prove the slot and captured backing absent"
        )
    matching_after = (
        tuple(item for item in after if item.slot_num == slot_num) if v_ok else ()
    )
    matching_backing_after = (
        tuple(item for item in backing_after if _same_backing_identity(item, captured))
        if b_ok
        else ()
    )
    result = VnicChangeResult(
        "remove",
        True,
        changed,
        selector,
        slot_num,
        selected,
        correlated,
        matching_after,
        matching_backing_after,
        v_ok,
        b_ok,
        output,
        tuple(errors),
    )
    if errors:
        raise VnicPartialError("vNIC remove could not be fully verified", result)
    return result
