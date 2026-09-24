"""Complete LPAR creation workflows shared by presentation adapters."""

from __future__ import annotations

from hmcpctl.client.core import HMCClient

from ...errors import HMCError
from .assignments import (
    LparPcieAssignments,
    LparPcieWorkflowResult,
    apply_validated_lpar_pcie_assignments,
    assignment_step_names,
    prevalidate_lpar_pcie_assignments,
)
from .core import LparCreation, create_and_stamp_lpar
from .errors import translate_lpar_write_error
from .workflow_contract import WorkflowStep


async def create_lpar(
    hmc: HMCClient,
    system_name_or_uuid: str,
    creation: LparCreation,
    assignments: LparPcieAssignments,
) -> LparPcieWorkflowResult:
    """Validate, create, stamp, and apply ordered PCIe assignments."""
    await prevalidate_lpar_pcie_assignments(hmc, system_name_or_uuid, assignments)
    try:
        created = await create_and_stamp_lpar(hmc, system_name_or_uuid, creation)
    except HMCError as exc:
        translated = translate_lpar_write_error(exc)
        if translated is exc:
            raise
        raise translated from exc
    steps = [WorkflowStep("create", "ok", created.lpar)]
    if created.apply_step is not None:
        steps.append(created.apply_step)
    # A failed apply, or a create that returns no partition body to apply against,
    # stops the ordered workflow; the partition (if created) stays created.
    apply_failed = created.apply_step is not None and created.apply_step.status == "error"
    assignments_skipped = created.lpar is None or apply_failed
    if assignments_skipped:
        steps.extend(
            WorkflowStep(name, "skipped") for name in assignment_step_names(assignments)
        )
    if assignments_skipped:
        return LparPcieWorkflowResult(
            True,
            False,
            created.lpar,
            created.ownership_stamped,
            tuple(steps),
            created.warnings,
        )
    assignment_result = await apply_validated_lpar_pcie_assignments(
        hmc, system_name_or_uuid, creation.name, assignments
    )
    steps.extend(assignment_result.steps)
    return LparPcieWorkflowResult(
        True,
        assignment_result.workflow_completed,
        created.lpar,
        created.ownership_stamped,
        tuple(steps),
        created.warnings,
    )
