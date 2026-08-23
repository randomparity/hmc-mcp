"""MCP tools for HMC/VIOS software update and firmware update jobs."""

from __future__ import annotations

from .tool_registry import tool_module

import re
from typing import Any, Literal, cast
from urllib.parse import quote

from ._app import (
    _run,
)

from .common import client_from_env, resolve_system_uuid, resolve_vios_uuid
from .errors import HMCError
from .jobs import (
    TERMINAL_JOB_STATUSES,
    ConsoleUpdateSource,
    PlatformUpdateParameter,
    VIOSSource,
    VIOSUpdateSource,
    VIOSUpgradeSource,
    update_hmc_job,
    list_management_console_updates_job,
    platform_update_job,
    update_vios_job,
    upgrade_vios_job,
    validate_wait_timing,
    vios_stdout,
    wait_for_submitted_job,
)


def _with_vios_stdout(
    result: dict[str, Any] | None, wait: bool
) -> dict[str, Any] | None:
    """Project completed VIOS job output without altering the raw job payload."""
    if not wait or not isinstance(result, dict) or "stdOut" in result:
        return result
    resource = result.get("Resource")
    if (
        not isinstance(resource, dict)
        or resource.get("Status") not in TERMINAL_JOB_STATUSES
    ):
        return result
    output = vios_stdout(result)
    if output is None:
        return result
    return {**result, "stdOut": output}


async def _update_op(
    hmc, submit_fn, wait: bool, timeout_seconds: int, poll_interval: int
) -> dict[str, Any] | None:
    """Submit an update/upgrade job on an already-open *hmc* client; optionally wait for terminal state."""
    job = await submit_fn(hmc)
    return await wait_for_submitted_job(hmc, job, wait, timeout_seconds, poll_interval)


async def _platform_update_op(
    hmc, submit_fn, wait: bool, timeout_seconds: int, poll_interval: int
) -> dict[str, Any] | None:
    """Submit PlatformUpdate without inventing an id-only polling endpoint."""
    job = await submit_fn(hmc)
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


_PLATFORM_UPDATE_VERSION = re.compile(r"V([0-9]{1,4})R([0-9]{1,4})M([0-9]{1,4})")
_MINIMUM_PLATFORM_UPDATE_VERSION = (11, 1, 1111)


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


tool, register_tools, tool_security = tool_module()


@tool(effect="destructive", operation="update.console", target_kind="console")
def hmc_update_console_software(
    console_uuid: str,
    repository: ConsoleUpdateSource,
    kind: Literal["update", "upgrade"] = "update",
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Submit a documented HMC software update job.

    kind='update' installs PTFs. ``upgrade`` is refused because IBM documents
    a multi-job upgrade workflow, not one ManagementConsole Upgrade operation.
    repository uses the documented UpdateManagementConsole parameter names::

        {"MediaType": "NFS", "ServerHostOrIP": "repo.example.com",
         "Directory": "/images/hmc", "RestartConsole": "False"}

    Submits UpdateManagementConsole to ManagementConsole; poll hmc_get_job
    for status. console_uuid is the ManagementConsole UUID (from
    hmc_console_info).

    Set wait=True to block until the job reaches a terminal state.

    Args:
        console_uuid: Management-console UUID returned by ``hmc_console_info``.
        repository: Documented ``UpdateManagementConsole`` job parameters.
        kind: ``update``; ``upgrade`` raises with multi-job workflow guidance.
        wait: Wait for the submitted job to reach a terminal state.
        timeout_seconds: Maximum wait duration in seconds.
        poll_interval: Seconds between job-status requests while waiting.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    if kind == "upgrade":
        raise ValueError(
            "HMC upgrades are not a single ManagementConsole job. Use the documented "
            "multi-job workflow beginning with SaveUpgradeData, followed by "
            "DownloadNetworkInstallImages, SetAlternateDiskStartup, and ShutdownHMC."
        )
    if kind != "update":
        raise ValueError(f"Unknown kind {kind!r}. Expected 'update' or 'upgrade'.")
    job_xml = update_hmc_job(repository)
    validate_wait_timing(wait, timeout_seconds, poll_interval)

    async def _go():
        async with client_from_env(profile) as hmc:
            console_path_id = quote(console_uuid, safe="")
            return await _update_op(
                hmc,
                lambda hmc2: hmc2.submit_job(
                    f"/rest/api/uom/ManagementConsole/{console_path_id}"
                    "/do/UpdateManagementConsole",
                    job_xml,
                ),
                wait,
                timeout_seconds,
                poll_interval,
            )

    return _run(_go)


@tool(effect="mutate", operation="update.list_ptfs", target_kind="console")
def hmc_get_available_hmc_ptfs(
    console_uuid: str,
    profile: str | None = None,
    *,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
) -> dict[str, Any] | None:
    """Submit the documented job that lists available HMC PTFs.

    The HMC obtains the list from the IBM website. With ``wait=False``, returns
    the submitted job so callers can poll it with ``hmc_get_job``. With
    ``wait=True``, polls until the job reaches a terminal state or the timeout
    expires; a completed job's response contains the available PTF objects.

    Args:
        console_uuid: Management-console UUID returned by ``hmc_console_info``.
        wait: Wait for the submitted job to reach a terminal state.
        timeout_seconds: Maximum wait duration in seconds.
        poll_interval: Seconds between job-status requests while waiting.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    validate_wait_timing(wait, timeout_seconds, poll_interval)
    job_xml = list_management_console_updates_job()

    async def _go():
        async with client_from_env(profile) as hmc:
            console_path_id = quote(console_uuid, safe="")
            return await _update_op(
                hmc,
                lambda hmc2: hmc2.submit_job(
                    f"/rest/api/uom/ManagementConsole/{console_path_id}"
                    "/do/ListManagementConsoleUpdates",
                    job_xml,
                ),
                wait,
                timeout_seconds,
                poll_interval,
            )

    return _run(_go)


@tool(effect="destructive", operation="update.vios", target_kind="vios")
def hmc_vios_update(
    vios_name_or_uuid: str,
    repository: VIOSSource,
    kind: Literal["update", "upgrade"] = "update",
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Submit a VIOS software update or upgrade job.

    kind='update' installs fixes (PTF level); kind='upgrade' performs a full
    VIOS version upgrade. repository uses the documented VIOS operation
    parameter names. Submits UpdateVIOS or UpgradeVIOS to VirtualIOServer; poll
    hmc_get_job for status.

    Set wait=True to block until the job reaches a terminal state.

    Args:
        vios_name_or_uuid: VIOS partition name or UUID from ``hmc_list_vios``.
        repository: Documented VIOS update or upgrade job parameters.
        kind: ``update`` for PTFs or ``upgrade`` for a full version upgrade.
        wait: Wait for the submitted job to reach a terminal state.
        timeout_seconds: Maximum wait duration in seconds.
        poll_interval: Seconds between job-status requests while waiting.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    if kind == "update":
        job_xml = update_vios_job(cast(VIOSUpdateSource, repository))
        operation = "UpdateVIOS"
    elif kind == "upgrade":
        job_xml = upgrade_vios_job(cast(VIOSUpgradeSource, repository))
        operation = "UpgradeVIOS"
    else:
        raise ValueError(f"Unknown kind {kind!r}. Expected 'update' or 'upgrade'.")
    validate_wait_timing(wait, timeout_seconds, poll_interval)

    async def _go():
        async with client_from_env(profile) as hmc:
            vios_uuid = await resolve_vios_uuid(hmc, vios_name_or_uuid)
            vios_path_id = quote(vios_uuid, safe="")
            result = await _update_op(
                hmc,
                lambda hmc2: hmc2.submit_job(
                    f"/rest/api/uom/VirtualIOServer/{vios_path_id}/do/{operation}",
                    job_xml,
                ),
                wait,
                timeout_seconds,
                poll_interval,
            )
            return _with_vios_stdout(result, wait)

    return _run(_go)


@tool(effect="destructive", operation="update.firmware", target_kind="managed_system")
def hmc_update_firmware(
    system_name_or_uuid: str,
    platform_update: PlatformUpdateParameter,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Submit a documented Power11 PlatformUpdate job.

    Requires HMC 11.1.1111 or later. ``platform_update`` explicitly selects
    system firmware, SR-IOV, VIOS, and IO-adapter work using IBM's nested JSON
    shape. Poll hmc_get_job for status when the HMC supplies a self link.

    Set wait=True to block until the job reaches a terminal state.

    Args:
        system_name_or_uuid: System name or UUID from ``hmc_list_systems``.
        platform_update: Strict documented PlatformUpdate parameter object.
        wait: Wait for the submitted job to reach a terminal state.
        timeout_seconds: Maximum wait duration in seconds.
        poll_interval: Seconds between job-status requests while waiting.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    validate_wait_timing(wait, timeout_seconds, poll_interval)

    async def _go():
        async with client_from_env(profile) as hmc:
            _require_platform_update_version(await hmc.get_console_info())
            system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
            return await _platform_update_op(
                hmc,
                lambda hmc2: hmc2.submit_platform_update(
                    system_uuid,
                    platform_update_job(platform_update),
                ),
                wait,
                timeout_seconds,
                poll_interval,
            )

    return _run(_go)
