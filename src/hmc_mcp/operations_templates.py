"""Shared partition-template deployment workflow."""

from __future__ import annotations

from typing import Any

from .client import HMCClient
from .common import resolve_system_uuid
from .errors import HMCError
from .error_translation import translate_template_error
from .jobs import validate_wait_timing, wait_for_submitted_job

MANUAL_TEMPLATE_STAMP_WARNING = (
    "ownership stamp not attempted: template deployment does not identify and stamp "
    "the new LPAR; list partitions to identify it, then set its description"
)


async def list_partition_templates(hmc: HMCClient) -> list[dict[str, Any]]:
    """List templates with domain-specific HMC error translation."""
    try:
        return await hmc.list_partition_templates()
    except HMCError as exc:
        translate_template_error(exc)
        raise


async def get_partition_template(
    hmc: HMCClient, template_uuid: str
) -> dict[str, Any] | None:
    """Fetch one template with domain-specific HMC error translation."""
    try:
        return await hmc.get_partition_template(template_uuid)
    except HMCError as exc:
        translate_template_error(exc)
        raise


async def deploy_partition_template(
    hmc: HMCClient,
    draft_template_uuid: str,
    target_system_name_or_uuid: str,
    *,
    wait: bool,
    timeout_seconds: int,
    poll_interval: int,
) -> dict[str, Any]:
    """Submit a template deployment and optionally wait for its terminal job."""
    validate_wait_timing(wait, timeout_seconds, poll_interval)
    target_system_uuid = await resolve_system_uuid(hmc, target_system_name_or_uuid)
    try:
        submitted_job = await hmc.deploy_partition_template(
            draft_template_uuid, target_system_uuid
        )
    except HMCError as exc:
        translate_template_error(exc)
        raise
    selected_job = await wait_for_submitted_job(
        hmc, submitted_job, wait, timeout_seconds, poll_interval
    )
    return {
        "job": selected_job,
        "warnings": [MANUAL_TEMPLATE_STAMP_WARNING],
    }
