"""Shared logical-partition and VIOS power-state vocabulary.

The values are ``LogicalPartitionState.Enum`` from the HMC's own schema
(``/rest/api/web/schema/inc/Enumerations.xsd``, read from a V10R3 HMC for
#1202). ``Unknown`` is capitalised there.
"""

from typing import Literal, get_args

PartitionState = Literal[
    "error",
    "not activated",
    "not available",
    "open firmware",
    "running",
    "shutting down",
    "starting",
    "migrating not active",
    "migrating running",
    "hardware discovery",
    "suspended",
    "suspending",
    "resuming",
    "Unknown",
]

PARTITION_STATES: frozenset[PartitionState] = frozenset(get_args(PartitionState))
