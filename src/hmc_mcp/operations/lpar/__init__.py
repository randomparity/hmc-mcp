"""Shared LPAR operation contracts and lifecycle entry points."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .assignments import (
        AssignmentResult,
        DedicatedPcieAssignment,
        LparPcieAssignments,
        LparPcieWorkflowResult,
        SriovLogicalPortAssignment,
        VnicAssignment,
        WorkflowStep,
        apply_lpar_pcie_assignments,
        apply_validated_lpar_pcie_assignments,
        prevalidate_lpar_pcie_assignments,
    )
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
    from .decommission import DecommissionResult, decommission_lpar
    from .provision import (
        AttachDiskResult,
        ProvisionNetwork,
        ProvisionResult,
        ProvisionStorage,
        attach_disk_to_lpar,
        provision_lpar,
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
    "resolve_and_authorize_lpar_mutation",
    "AssignmentResult",
    "DedicatedPcieAssignment",
    "LparPcieAssignments",
    "LparPcieWorkflowResult",
    "SriovLogicalPortAssignment",
    "VnicAssignment",
    "WorkflowStep",
    "apply_lpar_pcie_assignments",
    "apply_validated_lpar_pcie_assignments",
    "prevalidate_lpar_pcie_assignments",
    "DecommissionResult",
    "decommission_lpar",
    "AttachDiskResult",
    "ProvisionNetwork",
    "ProvisionResult",
    "ProvisionStorage",
    "attach_disk_to_lpar",
    "provision_lpar",
]

_OWNERSHIP_EXPORTS = frozenset({"resolve_and_authorize_lpar_mutation"})
_ASSIGNMENT_EXPORTS = frozenset(
    {
        "AssignmentResult",
        "DedicatedPcieAssignment",
        "LparPcieAssignments",
        "LparPcieWorkflowResult",
        "SriovLogicalPortAssignment",
        "VnicAssignment",
        "WorkflowStep",
        "apply_lpar_pcie_assignments",
        "apply_validated_lpar_pcie_assignments",
        "prevalidate_lpar_pcie_assignments",
    }
)
_DECOMMISSION_EXPORTS = frozenset({"DecommissionResult", "decommission_lpar"})
_PROVISION_EXPORTS = frozenset(
    {
        "AttachDiskResult",
        "ProvisionNetwork",
        "ProvisionResult",
        "ProvisionStorage",
        "attach_disk_to_lpar",
        "provision_lpar",
    }
)
_CORE_EXPORTS = (
    frozenset(__all__)
    - _OWNERSHIP_EXPORTS
    - _ASSIGNMENT_EXPORTS
    - _DECOMMISSION_EXPORTS
    - _PROVISION_EXPORTS
)


def __getattr__(name: str) -> Any:
    """Load the core seam without making lightweight submodules import it."""
    if name in _CORE_EXPORTS:
        from . import core

        return getattr(core, name)
    if name in _OWNERSHIP_EXPORTS:
        from . import ownership

        return getattr(ownership, name)
    if name in _ASSIGNMENT_EXPORTS:
        from . import assignments

        return getattr(assignments, name)
    if name in _DECOMMISSION_EXPORTS:
        from . import decommission

        return getattr(decommission, name)
    if name in _PROVISION_EXPORTS:
        from . import provision

        return getattr(provision, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
