"""MCP adapter for the end-to-end LPAR provisioning operation."""

from __future__ import annotations

from ..._app import with_client
from ...documents import LparResources, PartitionType
from ...operations.affinity import ProvisionAffinityAssessment
from ...operations.lpar.assignments import LparPcieAssignments
from ...operations.lpar.provision import (
    ProvisionAdapters,
    ProvisionResult,
    ProvisionStorage,
    provision_lpar,
)
from ...ssh.affinity import MinimumAffinityPolicy
from ...tool_registry import tool_module

tool, register_tools, tool_security = tool_module()


# The VIOS identities this call mutates arrive one level below the signature —
# `storage.vios_uuid` and `network.vios_partition_id` — and are declared here as
# nested selectors (#260), so extraction, the audit record, and denial messages
# see them instead of only the managed system. The tool remains
# `exhaustive_targets=False`: `storage.vios_uuid` is a fleet-unique UUID a policy
# `targets` table could bound, but `network.vios_partition_id` is a per-system
# slot number no allowlist can write precisely (ADR 0039, #259), so only
# `targets = "all-targets"` grants it today. The declaration is what makes the
# boundable half fixable the moment #259 gives the slot number a fleet-unique form.
@tool(
    effect="mutate",
    operation="provision.lpar",
    target_kind="managed_system",
    extra_targets=(
        ("vios", "network.vios_partition_id"),
        ("vios", "storage.vios_uuid"),
    ),
    exhaustive_targets=False,
)
def hmc_provision_lpar(
    system_name_or_uuid: str,
    name: str,
    network: ProvisionAdapters,
    storage: ProvisionStorage,
    resources: LparResources = LparResources(
        min_memory=256,
        desired_memory=4096,
        max_memory=8192,
        desired_vcpus=1,
        max_vcpus=2,
    ),
    partition_type: PartitionType = "AIX/Linux",
    power_on: bool = True,
    dry_run: bool = False,
    assignments: LparPcieAssignments = LparPcieAssignments(),
    caller_token: str | None = None,
    minimum_affinity_policy: MinimumAffinityPolicy | None = None,
    affinity_assessment: ProvisionAffinityAssessment | None = None,
    profile: str | None = None,
) -> ProvisionResult:
    """Provision an LPAR with network, vSCSI storage, and optional power-on.

    Args:
        system_name_or_uuid: Target managed-system name or UUID.
        name: Name for the new logical partition.
        network: Virtual Ethernet and VIOS vSCSI attachment settings.
        storage: VIOS-backed storage mapping settings.
        resources: Memory and processor settings for the partition.
        partition_type: Partition environment: AIX/Linux, OS400, or VIOS.
        power_on: Power on the partition after configuration succeeds.
        dry_run: Validate preconditions without creating or changing resources.
        assignments: Declarative dedicated, direct SR-IOV, and vNIC requests.
        caller_token: Optional caller tracking reference embedded in the partition
            description as ``[caller <token>]`` after the ownership stamp (ADR 0064);
            1–64 printable ASCII characters, no whitespace or , = " [ ] \\.
        minimum_affinity_policy: Optional POWER11 score and deliberately selected
            action. Omission preserves HMC defaults; ``fail`` is never implicit.
        affinity_assessment: Optional target-bound captured evidence and explicit
            warning or fail response. Assessment waits for successful activation;
            omission preserves asynchronous power-on behavior.
        profile: Optional TOML profile name; uses environment defaults when omitted.

    Returns:
        A structured result with resource_created, workflow_completed, lpar_uuid,
        dry_run, ownership_stamped, steps, and warnings fields.
        With ``caller_token``, ``ownership_stamped=True`` confirms both the ownership
        stamp and the caller segment landed (one combined write); ``False`` means both
        were lost; ``None`` means the stamp was skipped — the reason is in ``warnings``.
    """

    return with_client(
        lambda hmc: provision_lpar(
            hmc,
            system_name_or_uuid=system_name_or_uuid,
            name=name,
            network=network,
            storage=storage,
            resources=resources,
            partition_type=partition_type,
            power_on=power_on,
            dry_run=dry_run,
            assignments=assignments,
            caller_token=caller_token,
            minimum_affinity_policy=minimum_affinity_policy,
            affinity_assessment=affinity_assessment,
        ),
        profile=profile,
    )
