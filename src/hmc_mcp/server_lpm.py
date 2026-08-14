"""MCP tools for Live Partition Mobility (LPM)."""

from __future__ import annotations

from typing import Any

from ._app import (
    _DESTRUCTIVE,
    _resolve_lpar_uuid,
    _run,
    mcp,
)

from .common import client_from_env
from .jobs import wait_for_submitted_job


async def _job_op(
    hmc, submit_fn, wait: bool, timeout_seconds: int, poll_interval: int
) -> dict[str, Any] | None:
    """Submit a job on an already-open *hmc* client; optionally wait for it to reach a terminal state.

    *submit_fn* is ``async (hmc) -> job_entry``.  When *wait* is False the
    submitted job entry is returned immediately.  When *wait* is True the job
    UUID is extracted and ``wait_for_job`` is called before returning.
    """
    job = await submit_fn(hmc)
    return await wait_for_submitted_job(hmc, job, wait, timeout_seconds, poll_interval)


@mcp.tool
def hmc_migrate_lpar(
    lpar_name_or_uuid: str,
    target_system: str,
    target_profile_name: str | None = None,
    wait_time: int | None = None,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Live-migrate (LPM) an LPAR to another managed system.

    lpar_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_lpars).
    Submits a Migrate job. target_system is the target managed-system name.
    Optionally pin the target profile / wait time. Poll hmc_get_job for status.
    Run hmc_migrate_validate_lpar first to pre-check.

    Set wait=True to block until the job reaches COMPLETED / FAILED / EXCEPTION
    (or until timeout_seconds elapses).
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            lpar_uuid = await _resolve_lpar_uuid(hmc, lpar_name_or_uuid)
            return await _job_op(
                hmc,
                lambda hmc2: hmc2.lpar_migrate(
                    lpar_uuid, target_system, target_profile_name, wait_time=wait_time
                ),
                wait,
                timeout_seconds,
                poll_interval,
            )

    return _run(_go)


@mcp.tool
def hmc_migrate_validate_lpar(
    lpar_name_or_uuid: str,
    target_system: str,
    target_profile_name: str | None = None,
    wait_time: int | None = None,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Validate whether an LPM migration of an LPAR to target_system would succeed.

    lpar_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_lpars).
    Set wait=True to block until the validation job reaches a terminal state.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            lpar_uuid = await _resolve_lpar_uuid(hmc, lpar_name_or_uuid)
            return await _job_op(
                hmc,
                lambda hmc2: hmc2.lpar_migrate_validate(
                    lpar_uuid, target_system, target_profile_name, wait_time=wait_time
                ),
                wait,
                timeout_seconds,
                poll_interval,
            )

    return _run(_go)


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_migrate_abort_lpar(
    lpar_name_or_uuid: str, profile: str | None = None
) -> dict[str, Any] | None:
    """Abort an in-progress LPM migration of an LPAR.

    lpar_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_lpars).
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            lpar_uuid = await _resolve_lpar_uuid(hmc, lpar_name_or_uuid)
            return await hmc.lpar_migrate_abort(lpar_uuid)

    return _run(_go)


@mcp.tool
def hmc_migrate_recover_lpar(
    lpar_name_or_uuid: str, profile: str | None = None
) -> dict[str, Any] | None:
    """Recover an LPAR after a failed LPM migration.

    lpar_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_lpars).
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            lpar_uuid = await _resolve_lpar_uuid(hmc, lpar_name_or_uuid)
            return await hmc.lpar_migrate_recover(lpar_uuid)

    return _run(_go)


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_remote_restart_lpar(
    lpar_name_or_uuid: str, target_system: str, profile: str | None = None
) -> dict[str, Any] | None:
    """Remote-restart a failed LPAR on another managed system.

    lpar_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_lpars).
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            lpar_uuid = await _resolve_lpar_uuid(hmc, lpar_name_or_uuid)
            return await hmc.lpar_remote_restart(lpar_uuid, target_system)

    return _run(_go)
