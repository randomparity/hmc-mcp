"""Read partition-shaped collections system by system across the fleet (ADR 0197)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from hmcpctl.client.client_resolution import system_operating
from hmcpctl.client.core import HMCClient
from hmcpctl.discovery_limits import MAX_PARENT_DISCOVERY_SYSTEMS
from hmcpctl.errors import HMCError
from hmcpctl.resource_identity import is_uuid


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
    return value if isinstance(value, str) and value.strip() else None


def _unreadable(system: object) -> UnreadableSystem | None:
    """None when the quick/All *system* summary can be read; otherwise its record."""
    summary = system if isinstance(system, dict) else {}
    uuid = summary.get("UUID")
    if (
        isinstance(uuid, str)
        and is_uuid(uuid)
        and system_operating(summary.get("State"))
    ):
        return None
    return UnreadableSystem(
        system_name=_text(summary.get("SystemName")),
        system_uuid=_text(uuid),
        state=_text(summary.get("State")),
        detailed_state=_text(summary.get("StateDetail")),
    )


async def _stopped_operating(hmc: HMCClient, uuid: str) -> UnreadableSystem | None:
    """The record for a system that left `operating` after the inventory read."""
    try:
        summaries = await hmc.list_system_summaries()
    except HMCError:
        return None
    for summary in summaries:
        if isinstance(summary, dict) and summary.get("UUID") == uuid:
            return _unreadable(summary)
    return None


async def read_fleet(
    hmc: HMCClient,
    read_system: Callable[[str], Awaitable[list[dict[str, Any]]]],
    resources: str,
) -> FleetListing:
    """Read each operating system's scoped feed, naming every system skipped.

    The HMC-wide partition and VIOS feeds time out while any one system has no
    connection, where the other systems' scoped feeds still answer (#1293). So
    does the full ManagedSystem feed, so the systems come from quick/All (#1309).
    """
    systems = await hmc.list_system_summaries()
    if len(systems) > MAX_PARENT_DISCOVERY_SYSTEMS:
        raise ValueError(
            f"Cannot list {resources} across managed systems: discovery exceeds "
            f"{MAX_PARENT_DISCOVERY_SYSTEMS} managed systems; supply managed-system scope"
        )
    entries: list[dict[str, Any]] = []
    unreadable: list[UnreadableSystem] = []
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
