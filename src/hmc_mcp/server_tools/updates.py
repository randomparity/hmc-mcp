"""MCP tools for HMC/VIOS software update and firmware update jobs."""

from __future__ import annotations

from ..tool_registry import tool_module

from typing import Any, Literal, cast
from urllib.parse import quote

from .._app import (
    run_sync,
)

from ..client.client_factory import client_from_env
from ..resource_identity import resolve_system_uuid, resolve_vios_uuid
from ..jobs import validate_wait_timing
from ..operations.updates import (
    _require_platform_update_version,
    _submit_platform_update,
    _submit_update,
    _with_vios_stdout,
)
from ..update_jobs import (
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
            return await _submit_update(
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

    return run_sync(_go)


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
            return await _submit_update(
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

    return run_sync(_go)


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
            result = await _submit_update(
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

    return run_sync(_go)


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
            return await _submit_platform_update(
                hmc,
                lambda hmc2: hmc2.submit_platform_update(
                    system_uuid,
                    platform_update_job(platform_update),
                ),
                wait,
                timeout_seconds,
                poll_interval,
            )

    return run_sync(_go)
