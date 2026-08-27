"""Shared LPAR operation contracts and lifecycle entry points."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .core import (
        PARTITION_STATES,
        PROCESSOR_COMPATIBILITY_MODES,
        LparCreation,
        LparCreationResult,
        LparPowerOnOutcome,
        LparPowerResult,
        PartitionState,
        ProcessorCompatibilityMode,
        activation_allows_assessment,
        create_and_stamp_lpar,
        delete_lpar,
        power_lpar,
        power_on_outcome,
        rename_lpar,
    )

__all__ = [
    "PARTITION_STATES",
    "PROCESSOR_COMPATIBILITY_MODES",
    "LparCreation",
    "LparCreationResult",
    "LparPowerOnOutcome",
    "LparPowerResult",
    "PartitionState",
    "ProcessorCompatibilityMode",
    "activation_allows_assessment",
    "create_and_stamp_lpar",
    "delete_lpar",
    "power_lpar",
    "power_on_outcome",
    "rename_lpar",
]

_CORE_EXPORTS = frozenset(__all__)


def __getattr__(name: str) -> Any:
    """Load the core seam without making lightweight submodules import it."""
    if name not in _CORE_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from . import core

    return getattr(core, name)
