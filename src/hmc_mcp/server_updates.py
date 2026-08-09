"""MCP tools for HMC/VIOS software update and firmware update jobs.
"""

from __future__ import annotations

from typing import Any, Literal

from ._app import (
    _READ_ONLY,
    mcp,
    with_client,
)

from .jobs import (
    RepositorySource,
    update_firmware_job,
    update_hmc_job,
    upgrade_hmc_job,
    update_vios_job,
    upgrade_vios_job,
)



@mcp.tool
def hmc_hmc_update(
    system_uuid: str,
    repository: RepositorySource,
    kind: Literal["update", "upgrade"] = "update",
) -> dict[str, Any] | None:
    """Submit an HMC software update or upgrade job.

    kind='update' installs PTFs (patch level); kind='upgrade' performs a full
    HMC version upgrade. repository is a dict describing the software source:
        {"type": "nfs", "host": "repo.example.com", "path": "/images/hmc"}
        {"type": "sftp", "host": "repo.example.com", "path": "/hmc", "user": "admin", "sftp_pw": "..."}
        {"type": "disk"}  # use files already on the HMC disk

    Submits an Update or Upgrade job to ManagementConsole; poll hmc_get_job
    for status. system_uuid is the ManagementConsole UUID (from hmc_console_info).
    """
    if kind == "update":
        job_xml = update_hmc_job(repository)
        operation = "Update"
    elif kind == "upgrade":
        job_xml = upgrade_hmc_job(repository)
        operation = "Upgrade"
    else:
        raise ValueError(f"Unknown kind {kind!r}. Expected 'update' or 'upgrade'.")

    return with_client(
        lambda hmc: hmc.submit_job(
            f"/rest/api/uom/ManagementConsole/{system_uuid}/do/{operation}",
            job_xml,
        )
    )


@mcp.tool(annotations=_READ_ONLY)
def hmc_get_available_hmc_ptfs(system_uuid: str) -> dict[str, Any] | None:
    """Get available PTFs (fixes) for the HMC software.

    Issues a GET to the ManagementConsole resource with the SoftwareUpdate
    group, which returns available PTF information. system_uuid is the
    ManagementConsole UUID (from hmc_console_info). Does not submit a job.
    """

    return with_client(
        lambda hmc: hmc.get_uom("ManagementConsole", system_uuid, group="SoftwareUpdate")
    )


@mcp.tool
def hmc_vios_update(
    vios_uuid: str,
    repository: RepositorySource,
    kind: Literal["update", "upgrade"] = "update",
) -> dict[str, Any] | None:
    """Submit a VIOS software update or upgrade job.

    kind='update' installs fixes (PTF level); kind='upgrade' performs a full
    VIOS version upgrade. repository describes the image source (same format as
    hmc_hmc_update). Submits an Update or Upgrade job to VirtualIOServer; poll
    hmc_get_job for status. vios_uuid is the VIOS UUID (from hmc_vios).
    """
    if kind == "update":
        job_xml = update_vios_job(repository)
        operation = "Update"
    elif kind == "upgrade":
        job_xml = upgrade_vios_job(repository)
        operation = "Upgrade"
    else:
        raise ValueError(f"Unknown kind {kind!r}. Expected 'update' or 'upgrade'.")

    return with_client(
        lambda hmc: hmc.submit_job(
            f"/rest/api/uom/VirtualIOServer/{vios_uuid}/do/{operation}",
            job_xml,
        )
    )


@mcp.tool
def hmc_update_firmware(system_uuid: str, repository: RepositorySource) -> dict[str, Any] | None:
    """Submit a managed system firmware update job.

    repository describes the firmware image source (same format as
    hmc_hmc_update). Submits an UpdateFirmware job to ManagedSystem; poll
    hmc_get_job for status. system_uuid is the managed system UUID
    (from hmc_systems).
    """

    return with_client(
        lambda hmc: hmc.submit_job(
            f"/rest/api/uom/ManagedSystem/{system_uuid}/do/UpdateFirmware",
            update_firmware_job(repository),
        )
    )


