"""Validation and work limits for ambiguous resource resolution."""

from typing import Any

MAX_PARENT_DISCOVERY_SYSTEMS = 100


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
