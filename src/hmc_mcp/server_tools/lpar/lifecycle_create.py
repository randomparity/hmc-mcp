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

    system_name_or_uuid: the target managed system — accepts either a
    SystemName or a UUID. Memory values are in MiB. By default a
    shared-processor partition is created; set dedicated=True for dedicated
    CPUs (then procs are whole CPU counts). For shared partitions, procs are
    processing units (may be fractional, e.g. 0.5) and vcpus are virtual
    processor counts.

    The partition is created powered off with a default profile; storage,
    network and boot settings still need to be configured before it can boot an
    OS. This creates a real partition — confirm the target before calling.

    Raises ValueError if a partition with the given name already exists on any
    managed system — names must be unique across the HMC.

    Returns a dict with ``lpar``, ``ownership_stamped``, and ``warnings`` keys.

    Args:
        system_name_or_uuid: SystemName or UUID of the managed system to create on.
        name: Unique PartitionName for the new logical partition.
        resources: Memory and processor assignments for the new partition.
        partition_type: Partition type: AIX/Linux, OS400, or Virtual IO Server.
        partition_id: Optional numeric partition ID; the HMC assigns one when omitted.
        os_type: Optional target operating-system family: aix, linux, or ibmi.
        keylock: Optional initial keylock position: normal, manual, or auto.
        max_virtual_slots: Optional maximum number of virtual I/O slots.
        caller_token: Optional caller tracking reference embedded in the partition
            description after the ownership stamp (ADR 0064).
        assignments: Declarative dedicated, direct SR-IOV, and vNIC requests.
        profile: Optional configured HMC profile name; uses the default when omitted.
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
