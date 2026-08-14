"""Shared partition-template deployment workflow."""

from __future__ import annotations

from typing import Any

from .client import HMCClient
from .errors import HMCError
from .error_translation import translate_template_error
from .jobs import validate_wait_timing, wait_for_submitted_job

MANUAL_TEMPLATE_STAMP_WARNING = (
    "ownership stamp not attempted: template deployment does not identify and stamp "
    "the new LPAR; identify it with hmc_list_lpars and call hmc_set_lpar_description"
)


async def deploy_partition_template(
    hmc: HMCClient,
    draft_template_uuid: str,
    target_system_uuid: str,
    *,
    wait: bool,
    timeout_seconds: int,
    poll_interval: int,
) -> dict[str, Any]:
    """Submit a template deployment and optionally wait for its terminal job."""
    validate_wait_timing(wait, timeout_seconds, poll_interval)
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
