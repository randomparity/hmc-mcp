"""Read partition-shaped collections system by system across the fleet (ADR 0197)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from hmcpctl.client.core import HMCClient
from hmcpctl.discovery_limits import MAX_PARENT_DISCOVERY_SYSTEMS
from hmcpctl.errors import HMCError


@dataclass(frozen=True)
class UnreadableSystem:
    """A managed system a listing skipped, with the state that made it skip."""

    system_name: str | None
    system_uuid: str | None
    state: str | None
    detailed_state: str | None


@dataclass(frozen=True)
class FleetListing:
    """Entries read, and every managed system whose entries are absent from them."""

    entries: list[dict[str, Any]]
    unreadable_systems: list[UnreadableSystem] = field(default_factory=list)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _unreadable(system: dict[str, Any]) -> UnreadableSystem | None:
    """None when *system* can be read; otherwise the record naming it."""
    resource = system.get("Resource") or {}
    uuid = system.get("UUID")
    state = resource.get("State")
    if (
        isinstance(uuid, str)
        and uuid
        and isinstance(state, str)
        and state.strip().lower() == "operating"
    ):
        return None
    return UnreadableSystem(
        system_name=_text(resource.get("SystemName")),
        system_uuid=_text(uuid) or None,
        state=_text(state),
        detailed_state=_text(resource.get("DetailedState")),
    )


async def _stopped_operating(hmc: HMCClient, uuid: str) -> UnreadableSystem | None:
    """The record for a system that left `operating` after the inventory read."""
    try:
        fresh = await hmc.get_managed_system(uuid)
    except HMCError:
        return None
    return _unreadable(fresh) if fresh is not None else None


async def read_fleet(
    hmc: HMCClient,
    read_system: Callable[[str], Awaitable[list[dict[str, Any]]]],
    resources: str,
) -> FleetListing:
    """Read each operating system's scoped feed, naming every system skipped.

    The HMC-wide partition and VIOS feeds time out while any one system has no
    connection, where the other systems' scoped feeds still answer (#1293).
    """
    systems, unresolved = await hmc.inventory_managed_systems()
    if len(systems) > MAX_PARENT_DISCOVERY_SYSTEMS:
        raise ValueError(
            f"Cannot list {resources} across managed systems: discovery exceeds "
            f"{MAX_PARENT_DISCOVERY_SYSTEMS} managed systems; supply managed-system scope"
        )
    entries: list[dict[str, Any]] = []
    unreadable = [UnreadableSystem(name, uuid, None, None) for uuid, name in unresolved]
    for system in systems:
        skipped = _unreadable(system)
        if skipped is None:
            try:
                entries.extend(await read_system(system["UUID"]))
                continue
            except HMCError:
                skipped = await _stopped_operating(hmc, system["UUID"])
                if skipped is None:
                    raise
        unreadable.append(skipped)
    return FleetListing(entries, unreadable)
