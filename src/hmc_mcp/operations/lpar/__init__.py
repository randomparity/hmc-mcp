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
    from .ownership import resolve_and_authorize_lpar_mutation

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
    "resolve_and_authorize_lpar_mutation",
]

_OWNERSHIP_EXPORTS = frozenset({"resolve_and_authorize_lpar_mutation"})
_CORE_EXPORTS = frozenset(__all__) - _OWNERSHIP_EXPORTS


def __getattr__(name: str) -> Any:
    """Load the core seam without making lightweight submodules import it."""
    if name in _CORE_EXPORTS:
        from . import core

        return getattr(core, name)
    if name in _OWNERSHIP_EXPORTS:
        from . import ownership

        return getattr(ownership, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
