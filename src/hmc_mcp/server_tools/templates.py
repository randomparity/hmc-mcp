"""MCP tools for the partition template library."""

from __future__ import annotations

from ..tool_registry import tool_module

from typing import Any

from .._app import (
    with_client,
)
from ..operations.templates import (
    deploy_partition_template,
    get_partition_template,
    list_partition_templates,
)


tool, register_tools, tool_security = tool_module()


@tool(effect="read", operation="template.list", target_kind="console")
def hmc_list_partition_templates(profile: str | None = None) -> list[dict[str, Any]]:
    """List all partition templates in the HMC template library.

    Args:
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    return with_client(list_partition_templates, profile=profile)


@tool(effect="read", operation="template.get", target_kind="template")
def hmc_get_partition_template(
    template_uuid: str, profile: str | None = None
) -> dict[str, Any] | None:
    """Get one partition template by UUID.

    Returns None only for an empty successful response. HTTP 404 and other
    REST failures raise HMCError.

    Args:
        template_uuid: Partition-template UUID from ``hmc_list_partition_templates``.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    return with_client(
        lambda hmc: get_partition_template(hmc, template_uuid), profile=profile
    )


@tool(effect="mutate", operation="template.deploy", target_kind="managed_system")
def hmc_deploy_partition_template(
    draft_template_uuid: str,
    target_system_name_or_uuid: str,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
) -> dict[str, Any]:
    """Deploy a partition from a *draft* partition template.

    draft_template_uuid is the transformed/replica template UUID (produced by
    capture/transform). The target managed system accepts a name or UUID.
    Submits a Deploy job; poll hmc_get_job for status.

    Set wait=True to block until the job reaches a terminal state.

    With wait=True, a completed deployment compares the target system's LPARs
    before and after the job and stamps the sole new partition automatically.
    Check ownership_stamped and warnings: ambiguous or unavailable snapshots
    require the caller to identify and stamp the partition manually.

    Args:
        draft_template_uuid: Draft/replica template UUID produced by capture and
            transformation.
        target_system_name_or_uuid: Destination system name or UUID.
        wait: Wait for deployment to reach a terminal state and attempt ownership
            stamping of the sole newly observed partition.
        timeout_seconds: Maximum wait duration in seconds.
        poll_interval: Seconds between job-status requests while waiting.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    return with_client(
        lambda hmc: deploy_partition_template(
            hmc,
            draft_template_uuid,
            target_system_name_or_uuid,
            wait=wait,
            timeout_seconds=timeout_seconds,
            poll_interval=poll_interval,
        ),
        profile=profile,
    )
