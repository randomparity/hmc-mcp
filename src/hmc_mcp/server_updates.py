"""MCP tools for HMC/VIOS software update and firmware update jobs."""

from __future__ import annotations

from .tool_registry import tool_module

from typing import Any, Literal, cast
from urllib.parse import quote

from ._app import (
    _run,
)

from .common import client_from_env, resolve_system_uuid, resolve_vios_uuid
from .errors import HMCError
from .jobs import (
    ConsoleUpdateSource,
    RepositorySource,
    VIOSSource,
    VIOSUpdateSource,
    VIOSUpgradeSource,
    update_firmware_job,
    update_hmc_job,
    update_vios_job,
    upgrade_vios_job,
    validate_wait_timing,
    wait_for_submitted_job,
)


async def _update_op(
    hmc, submit_fn, wait: bool, timeout_seconds: int, poll_interval: int
) -> dict[str, Any] | None:
    """Submit an update/upgrade job on an already-open *hmc* client; optionally wait for terminal state."""
    job = await submit_fn(hmc)
    return await wait_for_submitted_job(hmc, job, wait, timeout_seconds, poll_interval)


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


def _check_ptf_error(exc: HMCError) -> None:
    """Re-raise *exc* with an actionable message if SoftwareUpdate group is unsupported.

    HTTP 400 with REST0026 indicates the SoftwareUpdate attribute group is not
    supported on this HMC version or firmware level. All other errors are left unchanged.
    """
    if exc.status_code == 400:
        msg_str = str(exc)
        body_str = exc.body or ""
        if (
            "REST0026" in msg_str
            or "REST0026" in body_str
            or "SoftwareUpdate" in msg_str
            or "SoftwareUpdate" in body_str
        ):
            raise HMCError(
                "SoftwareUpdate attribute group not supported on this HMC version.",
                exc.status_code,
            ) from exc


@tool(effect="read", operation="update.list_ptfs", target_kind="console")
def hmc_get_available_hmc_ptfs(
    console_uuid: str, profile: str | None = None
) -> dict[str, Any] | None:
    """Get available PTFs (fixes) for the HMC software.

    Issues a GET to the ManagementConsole resource with the SoftwareUpdate
    group, which returns available PTF information. console_uuid is the
    ManagementConsole UUID (from hmc_console_info). Does not submit a job.

    Args:
        console_uuid: Management-console UUID returned by ``hmc_console_info``.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.get_uom(
                "ManagementConsole", console_uuid, group="SoftwareUpdate"
            )

    try:
        return _run(_go)
    except HMCError as exc:
        _check_ptf_error(exc)
        raise


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
            return await _update_op(
                hmc,
                lambda hmc2: hmc2.submit_job(
                    f"/rest/api/uom/VirtualIOServer/{vios_path_id}/do/{operation}",
                    job_xml,
                ),
                wait,
                timeout_seconds,
                poll_interval,
            )

    return _run(_go)


@tool(effect="destructive", operation="update.firmware", target_kind="managed_system")
def hmc_update_firmware(
    system_name_or_uuid: str,
    repository: RepositorySource,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Submit a managed system firmware update job.

    repository describes the firmware image source (same format as
    hmc_update_console_software). Submits an UpdateFirmware job to ManagedSystem; poll
    hmc_get_job for status.

    Set wait=True to block until the job reaches a terminal state.

    Args:
        system_name_or_uuid: System name or UUID from ``hmc_list_systems``.
        repository: NFS, SFTP, or HMC-disk firmware source configuration.
        wait: Wait for the submitted job to reach a terminal state.
        timeout_seconds: Maximum wait duration in seconds.
        poll_interval: Seconds between job-status requests while waiting.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    validate_wait_timing(wait, timeout_seconds, poll_interval)

    async def _go():
        async with client_from_env(profile) as hmc:
            system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
            return await _update_op(
                hmc,
                lambda hmc2: hmc2.submit_job(
                    f"/rest/api/uom/ManagedSystem/{system_uuid}/do/UpdateFirmware",
                    update_firmware_job(repository),
                ),
                wait,
                timeout_seconds,
                poll_interval,
            )

    return _run(_go)
