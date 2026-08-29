"""Result-shape helpers shared by live-test scenario families."""

from __future__ import annotations

from typing import Any


def entries(data: Any) -> list[dict]:
    """Normalize a tool result to a flat list of entry dictionaries."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("entries", [])
    return []


def resource(entry: dict) -> dict:
    """Return the nested Resource dictionary, or the entry itself."""
    return entry.get("Resource") or entry
