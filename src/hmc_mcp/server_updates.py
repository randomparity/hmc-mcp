"""MCP tools for HMC/VIOS software update and firmware update jobs."""

from __future__ import annotations

from typing import Any, Literal

from ._app import (
    _READ_ONLY,
    _run,
    mcp,
)

from .common import client_from_env, resolve_system_uuid, resolve_vios_uuid
from .errors import HMCError
from .jobs import (
    RepositorySource,
    update_firmware_job,
    update_hmc_job,
    upgrade_hmc_job,
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


@mcp.tool
def hmc_update_console_software(
    console_uuid: str,
    repository: RepositorySource,
    kind: Literal["update", "upgrade"] = "update",
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Submit an HMC software update or upgrade job.

    kind='update' installs PTFs (patch level); kind='upgrade' performs a full
    HMC version upgrade. repository is a dict describing the software source:
        {"type": "nfs", "host": "repo.example.com", "path": "/images/hmc"}
        {"type": "sftp", "host": "repo.example.com", "path": "/hmc", "user": "admin", "sftp_pw": "..."}
        {"type": "disk"}  # use files already on the HMC disk

    Submits an Update or Upgrade job to ManagementConsole; poll hmc_get_job
    for status. console_uuid is the ManagementConsole UUID (from
    hmc_console_info).

    Set wait=True to block until the job reaches a terminal state.
    """
    if kind == "update":
        job_xml = update_hmc_job(repository)
        operation = "Update"
    elif kind == "upgrade":
        job_xml = upgrade_hmc_job(repository)
        operation = "Upgrade"
    else:
        raise ValueError(f"Unknown kind {kind!r}. Expected 'update' or 'upgrade'.")
    validate_wait_timing(wait, timeout_seconds, poll_interval)

    async def _go():
        async with client_from_env(profile) as hmc:
            return await _update_op(
                hmc,
                lambda hmc2: hmc2.submit_job(
                    f"/rest/api/uom/ManagementConsole/{console_uuid}/do/{operation}",
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


@mcp.tool(annotations=_READ_ONLY)
def hmc_get_available_hmc_ptfs(
    console_uuid: str, profile: str | None = None
) -> dict[str, Any] | None:
    """Get available PTFs (fixes) for the HMC software.

    Issues a GET to the ManagementConsole resource with the SoftwareUpdate
    group, which returns available PTF information. console_uuid is the
    ManagementConsole UUID (from hmc_console_info). Does not submit a job.
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


@mcp.tool
def hmc_vios_update(
    vios_name_or_uuid: str,
    repository: RepositorySource,
    kind: Literal["update", "upgrade"] = "update",
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Submit a VIOS software update or upgrade job.

    vios_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_vios).
    kind='update' installs fixes (PTF level); kind='upgrade' performs a full
    VIOS version upgrade. repository describes the image source (same format as
    hmc_update_console_software). Submits an Update or Upgrade job to VirtualIOServer; poll
    hmc_get_job for status.

    Set wait=True to block until the job reaches a terminal state.
    """
    if kind == "update":
        job_xml = update_vios_job(repository)
        operation = "Update"
    elif kind == "upgrade":
        job_xml = upgrade_vios_job(repository)
        operation = "Upgrade"
    else:
        raise ValueError(f"Unknown kind {kind!r}. Expected 'update' or 'upgrade'.")
    validate_wait_timing(wait, timeout_seconds, poll_interval)

    async def _go():
        async with client_from_env(profile) as hmc:
            vios_uuid = await resolve_vios_uuid(hmc, vios_name_or_uuid)
            return await _update_op(
                hmc,
                lambda hmc2: hmc2.submit_job(
                    f"/rest/api/uom/VirtualIOServer/{vios_uuid}/do/{operation}",
                    job_xml,
                ),
                wait,
                timeout_seconds,
                poll_interval,
            )

    return _run(_go)


@mcp.tool
def hmc_update_firmware(
    system_name_or_uuid: str,
    repository: RepositorySource,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Submit a managed system firmware update job.

    system_name_or_uuid: accepts either a SystemName or a UUID
    (find it with hmc_systems).
    repository describes the firmware image source (same format as
    hmc_update_console_software). Submits an UpdateFirmware job to ManagedSystem; poll
    hmc_get_job for status.

    Set wait=True to block until the job reaches a terminal state.
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
