"""MCP tools for Live Partition Mobility (LPM)."""

from __future__ import annotations

from ._app import (
    _DESTRUCTIVE,
    _run,
    mcp,
)

from .common import client_from_env
from .operations_lpm import (
    abort_lpar_migration,
    migrate_lpar,
    recover_lpar_migration,
    remote_restart_lpar,
)

from typing import Any


@mcp.tool
def hmc_migrate_lpar(
    lpar_name_or_uuid: str,
    target_system_name_or_uuid: str,
    target_profile_name: str | None = None,
    wait_time: int | None = None,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Live-migrate (LPM) an LPAR to another managed system.

    lpar_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_list_lpars).
    Submits a Migrate job. The target accepts a managed-system name or UUID.
    Optionally pin the target profile / wait time. Poll hmc_get_job for status.
    Run hmc_migrate_validate_lpar first to pre-check.

    Set wait=True to block until the job reaches COMPLETED / FAILED / EXCEPTION
    (or until timeout_seconds elapses).
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            result = await migrate_lpar(
                hmc,
                lpar_name_or_uuid,
                target_system_name_or_uuid,
                target_profile_name,
                wait_time,
                wait=wait,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
            )
            return result.job

    return _run(_go)


@mcp.tool
def hmc_migrate_validate_lpar(
    lpar_name_or_uuid: str,
    target_system_name_or_uuid: str,
    target_profile_name: str | None = None,
    wait_time: int | None = None,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Validate whether an LPM migration of an LPAR to target_system would succeed.

    lpar_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_list_lpars).
    Set wait=True to block until the validation job reaches a terminal state.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            result = await migrate_lpar(
                hmc,
                lpar_name_or_uuid,
                target_system_name_or_uuid,
                target_profile_name,
                wait_time,
                validate=True,
                wait=wait,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
            )
            return result.job

    return _run(_go)


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_migrate_abort_lpar(
    lpar_name_or_uuid: str, profile: str | None = None
) -> dict[str, Any] | None:
    """Abort an in-progress LPM migration of an LPAR.

    lpar_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_list_lpars).
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return (await abort_lpar_migration(hmc, lpar_name_or_uuid)).job

    return _run(_go)


@mcp.tool
def hmc_migrate_recover_lpar(
    lpar_name_or_uuid: str, profile: str | None = None
) -> dict[str, Any] | None:
    """Recover an LPAR after a failed LPM migration.

    lpar_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_list_lpars).
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return (await recover_lpar_migration(hmc, lpar_name_or_uuid)).job

    return _run(_go)


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_remote_restart_lpar(
    lpar_name_or_uuid: str,
    target_system_name_or_uuid: str,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Remote-restart a failed LPAR on another managed system.

    lpar_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_list_lpars).
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return (
                await remote_restart_lpar(
                    hmc, lpar_name_or_uuid, target_system_name_or_uuid
                )
            ).job

    return _run(_go)
