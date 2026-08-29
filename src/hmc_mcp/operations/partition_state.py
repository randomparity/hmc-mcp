"""Shared logical-partition and VIOS power-state vocabulary."""

from typing import Literal

PartitionState = Literal[
    "running",
    "not activated",
    "starting",
    "shutting down",
    "stopping",
    "open firmware",
    "error",
    "migrating",
    "suspended",
    "resuming",
    "unknown",
]

PARTITION_STATES: frozenset[PartitionState] = frozenset(
    {
        "running",
        "not activated",
        "starting",
        "shutting down",
        "stopping",
        "open firmware",
        "error",
        "migrating",
        "suspended",
        "resuming",
        "unknown",
    }
)
