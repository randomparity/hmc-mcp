"""Complete LPAR creation workflows shared by presentation adapters."""

from __future__ import annotations

from hmc_mcp.client.core import HMCClient

from ...errors import HMCError
from .assignments import (
    LparPcieAssignments,
    LparPcieWorkflowResult,
    apply_validated_lpar_pcie_assignments,
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
        raise translate_lpar_write_error(exc) from exc
    steps = [WorkflowStep("create", "ok", created.lpar)]
    if created.lpar is None:
        return LparPcieWorkflowResult(
            True,
            False,
            None,
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
