"""Presentation-neutral update submission and waiting workflows."""

from __future__ import annotations

import re
from typing import Any, Literal, cast
from urllib.parse import quote

from ..client import HMCClient
from ..errors import HMCError
from ..jobs import (
    TERMINAL_JOB_STATUSES,
    validate_wait_timing,
    vios_stdout,
    wait_for_submitted_job,
)
from ..resource_identity import resolve_system_uuid, resolve_vios_uuid
from ..update_jobs import (
    ConsoleUpdateSource,
    PlatformUpdateParameter,
    VIOSSource,
    VIOSUpdateSource,
    VIOSUpgradeSource,
    list_management_console_updates_job,
    platform_update_job,
    update_hmc_job,
    update_vios_job,
    upgrade_vios_job,
)

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
    hmc: HMCClient,
    job: dict[str, Any] | None,
    wait: bool,
    timeout_seconds: int,
    poll_interval: int,
) -> dict[str, Any] | None:
    """Submit an update job and honor its shared waiting contract."""
    return await wait_for_submitted_job(hmc, job, wait, timeout_seconds, poll_interval)


async def _submit_platform_update(
    hmc: HMCClient,
    job: dict[str, Any] | None,
    wait: bool,
    timeout_seconds: int,
    poll_interval: int,
) -> dict[str, Any] | None:
    """Submit PlatformUpdate and require a link before polling."""
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


async def update_console_software(
    hmc: HMCClient,
    console_uuid: str,
    repository: ConsoleUpdateSource,
    kind: Literal["update", "upgrade"] = "update",
    *,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
) -> dict[str, Any] | None:
    """Submit a supported management-console software update."""
    if kind == "upgrade":
        raise ValueError(
            "HMC upgrades are not a single ManagementConsole job. Use the documented "
            "multi-job workflow beginning with SaveUpgradeData, followed by "
            "DownloadNetworkInstallImages, SetAlternateDiskStartup, and ShutdownHMC."
        )
    if kind != "update":
        raise ValueError(f"Unknown kind {kind!r}. Expected 'update' or 'upgrade'.")
    validate_wait_timing(wait, timeout_seconds, poll_interval)
    console_path_id = quote(console_uuid, safe="")
    job = await hmc.submit_job(
        f"/rest/api/uom/ManagementConsole/{console_path_id}"
        "/do/UpdateManagementConsole",
        update_hmc_job(repository),
    )
    return await _submit_update(hmc, job, wait, timeout_seconds, poll_interval)


async def list_available_hmc_ptfs(
    hmc: HMCClient,
    console_uuid: str,
    *,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
) -> dict[str, Any] | None:
    """Submit the management-console job that lists available PTFs."""
    validate_wait_timing(wait, timeout_seconds, poll_interval)
    console_path_id = quote(console_uuid, safe="")
    job = await hmc.submit_job(
        f"/rest/api/uom/ManagementConsole/{console_path_id}"
        "/do/ListManagementConsoleUpdates",
        list_management_console_updates_job(),
    )
    return await _submit_update(hmc, job, wait, timeout_seconds, poll_interval)


async def update_vios(
    hmc: HMCClient,
    vios_name_or_uuid: str,
    repository: VIOSSource,
    kind: Literal["update", "upgrade"] = "update",
    *,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
) -> dict[str, Any] | None:
    """Submit a VIOS software update or upgrade and project terminal output."""
    if kind == "update":
        job_xml = update_vios_job(cast(VIOSUpdateSource, repository))
        operation = "UpdateVIOS"
    elif kind == "upgrade":
        job_xml = upgrade_vios_job(cast(VIOSUpgradeSource, repository))
        operation = "UpgradeVIOS"
    else:
        raise ValueError(f"Unknown kind {kind!r}. Expected 'update' or 'upgrade'.")
    validate_wait_timing(wait, timeout_seconds, poll_interval)
    vios_uuid = await resolve_vios_uuid(hmc, vios_name_or_uuid)
    vios_path_id = quote(vios_uuid, safe="")
    job = await hmc.submit_job(
        f"/rest/api/uom/VirtualIOServer/{vios_path_id}/do/{operation}", job_xml
    )
    result = await _submit_update(hmc, job, wait, timeout_seconds, poll_interval)
    return _with_vios_stdout(result, wait)


async def update_firmware(
    hmc: HMCClient,
    system_name_or_uuid: str,
    platform_update: PlatformUpdateParameter,
    *,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
) -> dict[str, Any] | None:
    """Submit a supported Power11 PlatformUpdate workflow."""
    validate_wait_timing(wait, timeout_seconds, poll_interval)
    _require_platform_update_version(await hmc.get_console_info())
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    job = await hmc.submit_platform_update(
        system_uuid, platform_update_job(platform_update)
    )
    return await _submit_platform_update(
        hmc, job, wait, timeout_seconds, poll_interval
    )
