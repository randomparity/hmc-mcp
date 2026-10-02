"""Validation and work limits for ambiguous resource resolution."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from ..discovery_limits import (
    MAX_PARENT_DISCOVERY_SYSTEMS,
    PARENT_DISCOVERY_TIMEOUT_SECONDS,
)
from ..errors import HMCError

_MAX_STATE_TEXT = 100


def ambiguity_candidate_ids(
    entries: list[dict[str, Any]], resource_label: str, name: str
) -> list[str]:
    """Return distinct candidate UUIDs or reject incomplete HMC metadata."""
    candidate_ids: list[str] = []
    for entry in entries:
        uuid = entry.get("UUID")
        if not isinstance(uuid, str) or not uuid:
            raise ValueError(
                f"Cannot resolve ambiguous {resource_label} name {name!r}: "
                "candidate UUID metadata is incomplete"
            )
        candidate_ids.append(uuid)
    if len(set(candidate_ids)) != len(candidate_ids):
        raise ValueError(
            f"Cannot resolve ambiguous {resource_label} name {name!r}: "
            "candidate UUID metadata is not distinct"
        )
    return candidate_ids


def bounded_parent_systems(
    systems: list[dict[str, Any]], resource_label: str, name: str
) -> list[dict[str, Any]]:
    """Reject parent discovery whose outbound request fan-out is too large."""
    if len(systems) > MAX_PARENT_DISCOVERY_SYSTEMS:
        raise ValueError(
            f"Cannot resolve ambiguous {resource_label} name {name!r}: parent "
            f"discovery exceeds {MAX_PARENT_DISCOVERY_SYSTEMS} managed systems; "
            "supply managed-system scope"
        )
    return systems


async def ambiguous_parent_details(
    entries: list[dict[str, Any]],
    systems: list[dict[str, Any]],
    resource_label: str,
    name: str,
    list_children: Callable[[str], Awaitable[list[dict[str, Any]]]],
) -> str:
    """Resolve each ambiguous child to exactly one managed-system parent."""
    candidate_ids = ambiguity_candidate_ids(entries, resource_label, name)
    parents: dict[str, list[tuple[str, str]]] = {uuid: [] for uuid in candidate_ids}
    bounded_systems = bounded_parent_systems(systems, resource_label, name)
    try:
        async with asyncio.timeout(PARENT_DISCOVERY_TIMEOUT_SECONDS):
            for system in bounded_systems:
                parent_uuid = system.get("UUID")
                parent_name = (system.get("Resource") or {}).get("SystemName")
                if (
                    not isinstance(parent_uuid, str)
                    or not parent_uuid
                    or not isinstance(parent_name, str)
                    or not parent_name
                ):
                    raise ValueError(
                        f"Cannot resolve ambiguous {resource_label} name {name!r}: "
                        "cannot identify managed system from incomplete inventory metadata"
                    )
                for entry in await list_children(parent_uuid):
                    entry_uuid = str(entry.get("UUID"))
                    if entry_uuid in parents:
                        parents[entry_uuid].append((parent_name, parent_uuid))
    except TimeoutError as exc:
        raise ValueError(
            f"Cannot resolve ambiguous {resource_label} name {name!r}: parent "
            "discovery timed out; supply managed-system scope"
        ) from exc
    invalid = sorted(uuid for uuid, matches in parents.items() if len(matches) != 1)
    if invalid:
        raise ValueError(
            f"Cannot resolve ambiguous {resource_label} name {name!r}: candidates "
            f"{', '.join(invalid)} must each belong to exactly one managed system"
        )
    return ", ".join(
        f"{uuid} on {parents[uuid][0][0]!r} ({parents[uuid][0][1]})"
        for uuid in sorted(
            candidate_ids,
            key=lambda value: (parents[value][0][0], parents[value][0][1], value),
        )
    )


def _state_text(value: object) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()[:_MAX_STATE_TEXT]
    return "unknown"


async def require_operating_system(
    get_managed_system: Callable[[str], Awaitable[dict[str, Any] | None]],
    system_uuid: str,
    resources: str,
) -> None:
    """Refuse to read an empty per-system feed as empty unless the system operates.

    A managed system in recovery or with no connection answers its partition
    and VIOS feeds with HTTP 204 and no body (#1289), which reads the same as
    a system with none.
    """
    system = await get_managed_system(system_uuid)
    resource = (system or {}).get("Resource") or {}
    state = _state_text(resource.get("State"))
    if state.lower() == "operating":
        return
    detailed = _state_text(resource.get("DetailedState"))
    name = _state_text(resource.get("SystemName"))
    system_label = system_uuid if name == "unknown" else f"{name!r} ({system_uuid})"
    raise HMCError(
        f"Cannot list {resources} on managed system {system_label}: it is in "
        f"State {state!r} (DetailedState {detailed!r}), and the HMC reports no "
        f"{resources} for a system that is not operating"
    )
