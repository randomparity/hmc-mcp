"""MCP tool for creating an LPAR."""

from __future__ import annotations

from ..._app import with_client
from ...documents import Keylock, LparResources, OsType, PartitionType
from ...operations.lpar.assignments import LparPcieAssignments, LparPcieWorkflowResult
from ...operations.lpar.core import LparCreation
from ...operations.lpar.workflows import create_lpar
from ...ssh.lpar import validate_caller_token
from ...tool_registry import tool_module

tool, register_tools, tool_security = tool_module()


# vNIC assignments name a nested VIOS that target extraction cannot authorize.
@tool(
    effect="mutate",
    operation="lpar.create",
    target_kind="managed_system",
    exhaustive_targets=False,
)
def hmc_create_lpar(
    system_name_or_uuid: str,
    name: str,
    resources: LparResources = LparResources(
        min_memory=256,
        desired_memory=4096,
        max_memory=8192,
        dedicated=False,
        desired_vcpus=1,
        max_vcpus=2,
        uncapped=True,
    ),
    partition_type: PartitionType = "AIX/Linux",
    partition_id: int | None = None,
    os_type: OsType | None = None,
    keylock: Keylock | None = None,
    max_virtual_slots: int | None = None,
    caller_token: str | None = None,
    assignments: LparPcieAssignments = LparPcieAssignments(),
    profile: str | None = None,
) -> LparPcieWorkflowResult:
    """Create a new LPAR on a managed system.

    See the parameter annotations and server schema for accepted values. The
    operation creates the partition powered off with the requested resources.
    """
    if caller_token is not None:
        validate_caller_token(caller_token)
    return with_client(
        lambda hmc: create_lpar(
            hmc,
            system_name_or_uuid,
            LparCreation(
                name,
                partition_type,
                resources,
                partition_id,
                os_type,
                keylock,
                max_virtual_slots,
                caller_token,
            ),
            assignments,
        ),
        profile=profile,
    )
