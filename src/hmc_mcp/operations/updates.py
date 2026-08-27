"""Presentation-neutral update submission and waiting workflows."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from ..errors import HMCError
from ..jobs import TERMINAL_JOB_STATUSES, vios_stdout, wait_for_submitted_job

_Submit = Callable[[Any], Awaitable[dict[str, Any] | None]]
_PLATFORM_UPDATE_VERSION = re.compile(r"V([0-9]{1,4})R([0-9]{1,4})M([0-9]{1,4})")
_MINIMUM_PLATFORM_UPDATE_VERSION = (11, 1, 1111)


def _with_vios_stdout(
    result: dict[str, Any] | None, wait: bool
) -> dict[str, Any] | None:
    """Project completed VIOS job output without altering the raw payload."""
    if not wait or not isinstance(result, dict) or "stdOut" in result:
        return result
    resource = result.get("Resource")
    if (
        not isinstance(resource, dict)
        or resource.get("Status") not in TERMINAL_JOB_STATUSES
    ):
        return result
    output = vios_stdout(result)
    return result if output is None else {**result, "stdOut": output}


async def _submit_update(
    hmc: Any,
    submit: _Submit,
    wait: bool,
    timeout_seconds: int,
    poll_interval: int,
) -> dict[str, Any] | None:
    """Submit an update job and honor its shared waiting contract."""
    job = await submit(hmc)
    return await wait_for_submitted_job(hmc, job, wait, timeout_seconds, poll_interval)


async def _submit_platform_update(
    hmc: Any,
    submit: _Submit,
    wait: bool,
    timeout_seconds: int,
    poll_interval: int,
) -> dict[str, Any] | None:
    """Submit PlatformUpdate and require a link before polling."""
    job = await submit(hmc)
    if not wait:
        return job
    if job is not None:
        resource = job.get("Resource")
        status = resource.get("Status") if isinstance(resource, dict) else None
        if isinstance(status, str) and status in TERMINAL_JOB_STATUSES:
            return job
        link = job.get("link")
        if not isinstance(link, str) or not link.strip():
            raise HMCError(
                "PlatformUpdate was accepted but cannot be polled because the HMC "
                "returned a nonterminal response without a selfLink"
            )
    return await wait_for_submitted_job(hmc, job, wait, timeout_seconds, poll_interval)


def _require_platform_update_version(console: dict[str, Any] | None) -> None:
    """Require documented PlatformUpdate support before resolving a target."""
    resource = console.get("Resource") if isinstance(console, dict) else None
    version = resource.get("VersionInfo") if isinstance(resource, dict) else None
    match = (
        _PLATFORM_UPDATE_VERSION.fullmatch(version)
        if isinstance(version, str)
        else None
    )
    parsed = tuple(int(part) for part in match.groups()) if match else None
    if parsed is None or parsed < _MINIMUM_PLATFORM_UPDATE_VERSION:
        classification = "below the minimum" if parsed is not None else "unavailable"
        raise ValueError(
            "PlatformUpdate requires HMC 11.1.1111 or later; "
            f"the connected HMC version is {classification}. "
            "Upgrade the HMC before retrying."
        )
