"""Dynamic LPAR resource mutation and ownership resolution."""

from __future__ import annotations

from typing import Any

from hmc_mcp.client.core import HMCClient
from hmc_mcp.operations.ownership import resolve_and_authorize_lpar_mutation

from ...documents import (
    LparResources,
    build_dlpar_mem_document,
    build_dlpar_proc_document,
    build_lpar_document,
)
from ...errors import HMCError
from .assignments import (
    LparPcieAssignments,
    LparPcieWorkflowResult,
    _apply_validated_lpar_pcie_assignments,
    prevalidate_lpar_pcie_assignments,
)
from .errors import translate_lpar_write_error
from .workflow_contract import WorkflowStep


async def modify_lpar(
    hmc: HMCClient,
    system_name_or_uuid: str | None,
    lpar_name_or_uuid: str,
    resources: LparResources,
    assignments: LparPcieAssignments,
    *,
    new_name: str | None = None,
    ownership_override: bool = False,
) -> LparPcieWorkflowResult:
    """Authorize and apply rename, resource, and PCIe changes in order."""
    if (
        assignments != LparPcieAssignments() or new_name is not None
    ) and system_name_or_uuid is None:
        raise ValueError("system_name_or_uuid is required for rename or PCIe assignments")
    if system_name_or_uuid is not None:
        await prevalidate_lpar_pcie_assignments(hmc, system_name_or_uuid, assignments)

    lpar_uuid = await resolve_and_authorize_lpar_mutation(
        hmc,
        system_name_or_uuid,
        lpar_name_or_uuid,
        ownership_override=ownership_override,
    )
    resource = None
    steps: list[WorkflowStep] = []
    if new_name is not None:
        resource = await hmc.modify_logical_partition(
            lpar_uuid, build_lpar_document(name=new_name)
        )
        steps.append(WorkflowStep("rename", "ok", resource))
    if resources != LparResources():
        try:
            resource = await hmc.modify_logical_partition(
                lpar_uuid, build_lpar_document(name=None, resources=resources)
            )
        except HMCError as exc:
            translate_lpar_write_error(exc)
            raise
        steps.append(WorkflowStep("resources", "ok", resource))

    assignment_result = await _apply_validated_lpar_pcie_assignments(
        hmc,
        system_name_or_uuid or "",
        lpar_uuid,
        assignments,
        ownership_override=ownership_override,
    )
    steps.extend(assignment_result.steps)
    if resource is None:
        resource = await hmc.get_logical_partition(lpar_uuid)
    return LparPcieWorkflowResult(
        False,
        assignment_result.workflow_completed,
        resource,
        None,
        tuple(steps),
        (),
    )


async def _apply_dlpar_document(
    hmc: HMCClient,
    lpar_name_or_uuid: str,
    document: str,
    system_name_or_uuid: str | None,
    ownership_override: bool,
) -> dict[str, Any] | None:
    """Authorize one partition, then POST a partial LogicalPartition document."""
    lpar_uuid = await resolve_and_authorize_lpar_mutation(
        hmc,
        system_name_or_uuid,
        lpar_name_or_uuid,
        ownership_override=ownership_override,
    )
    try:
        return await hmc.modify_logical_partition(lpar_uuid, document)
    except HMCError as exc:
        translate_lpar_write_error(exc)
        raise


async def set_lpar_processors(
    hmc: HMCClient,
    system_name_or_uuid: str | None,
    lpar_name_or_uuid: str,
    resources: LparResources,
    *,
    ownership_override: bool = False,
) -> dict[str, Any] | None:
    """Authorize and apply a DLPAR processor change to one partition.

    Posts a minimal ``PartitionProcessorConfiguration`` document: only the
    fields set on *resources* change, and the rest of the partition's processor
    configuration is left alone. For a shared partition ``procs`` are
    processing units (fractional values such as ``0.5`` are valid) and
    ``vcpus`` are virtual processor counts; set ``dedicated=True`` for
    whole-CPU assignment, ``False`` for shared, and leave it unset to keep the
    current sharing mode.

    If the partition has no active RMC connection the change is profile-only
    and takes effect on its next activation; no reboot is triggered either way.

    ADR 0092 §3.2 classifies this as Reconfiguring, so
    :func:`authorize_lpar_mutation` runs unconditionally before the write.
    *system_name_or_uuid* stays optional (ADR 0063): when it is omitted the
    owning managed system is discovered so the guard can still read the token
    (ADR 0094).
    """
    return await _apply_dlpar_document(
        hmc,
        lpar_name_or_uuid,
        build_dlpar_proc_document(resources),
        system_name_or_uuid,
        ownership_override,
    )


async def set_lpar_memory(
    hmc: HMCClient,
    system_name_or_uuid: str | None,
    lpar_name_or_uuid: str,
    resources: LparResources,
    *,
    ownership_override: bool = False,
) -> dict[str, Any] | None:
    """Authorize and apply a DLPAR memory change to one partition.

    Posts a minimal ``PartitionMemoryConfiguration`` document: memory values
    are in MiB, only the fields set on *resources* change, and the processor
    fields of *resources* are ignored.

    If the partition has no active RMC connection the change is profile-only
    and takes effect on its next activation; no reboot is triggered either way.

    ADR 0092 §3.2 classifies this as Reconfiguring, so
    :func:`authorize_lpar_mutation` runs unconditionally before the write.
    *system_name_or_uuid* stays optional (ADR 0063): when it is omitted the
    owning managed system is discovered so the guard can still read the token
    (ADR 0094).
    """
    return await _apply_dlpar_document(
        hmc,
        lpar_name_or_uuid,
        build_dlpar_mem_document(resources),
        system_name_or_uuid,
        ownership_override,
    )
