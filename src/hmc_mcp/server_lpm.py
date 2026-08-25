"""MCP tools for Live Partition Mobility (LPM)."""

from __future__ import annotations

from .tool_registry import tool_module

from ._app import (
    _run,
)

from .common import client_from_env
from .jobs import JobOutcome
from .operations_lpm import (
    LpmAffinityMigrationResult,
    LpmAffinityPreflightRequest,
    abort_lpar_migration,
    migrate_lpar,
    migrate_lpar_with_affinity_preflight,
    recover_lpar_migration,
    remote_restart_lpar,
)

from typing import cast

from .jobs import RemoteRestartOperation


tool, register_tools, tool_security = tool_module()


@tool(effect="mutate", operation="lpar.migrate", target_kind="lpar")
def hmc_migrate_lpar(
    lpar_name_or_uuid: str,
    target_system_name_or_uuid: str,
    target_profile_name: str | None = None,
    wait_time: int | None = None,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    validate_first: bool = True,
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
) -> JobOutcome:
    """Live-migrate (LPM) an LPAR to another managed system.

    lpar_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_list_lpars).
    By default, submits validation, waits for successful terminal validation,
    then submits migration. Validation failure, exception, or timeout raises
    without submitting migration. Set validate_first=False for direct submission.

    Set wait=True to block until the job reaches COMPLETED / FAILED / EXCEPTION
    (or until timeout_seconds elapses).

    Args:
        lpar_name_or_uuid: Source partition name or UUID.
        target_system_name_or_uuid: Destination managed-system name or UUID.
        target_profile_name: Optional destination partition profile name.
        wait_time: HMC migration wait time in seconds, or the HMC default when omitted.
        wait: Wait for the migration job's terminal outcome when true.
        timeout_seconds: Maximum client-side wait in seconds.
        poll_interval: Seconds between job-status requests while waiting.
        validate_first: Validate successfully before submitting migration when true.
        profile: Optional TOML profile name; uses environment defaults when omitted.
        system_name_or_uuid: Optional SystemName or UUID of the source system,
            disambiguating the partition name; when omitted the name is
            searched fleet-wide.
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
                validate_first=validate_first,
                system_name_or_uuid=system_name_or_uuid,
            )
            return cast(JobOutcome, result.job)

    return _run(_go)


@tool(effect="mutate", operation="lpar.migrate_affinity", target_kind="lpar")
def hmc_migrate_lpar_with_affinity_preflight(
    lpar_name_or_uuid: str,
    target_system_name_or_uuid: str,
    affinity_preflight: LpmAffinityPreflightRequest,
    target_profile_name: str | None = None,
    wait_time: int | None = None,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
) -> LpmAffinityMigrationResult:
    """Run explicit affinity preflight before validation-first LPM.

    The companion result always preserves the preflight evidence and decision.
    Explicit fail-closed intent prevents both HMC validation and migration when
    affinity evidence is adverse or unavailable.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await migrate_lpar_with_affinity_preflight(
                hmc,
                lpar_name_or_uuid,
                target_system_name_or_uuid,
                affinity_preflight,
                target_profile_name,
                wait_time,
                wait=wait,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
                system_name_or_uuid=system_name_or_uuid,
            )

    return _run(_go)


@tool(effect="mutate", operation="lpar.migrate_validate", target_kind="lpar")
def hmc_migrate_validate_lpar(
    lpar_name_or_uuid: str,
    target_system_name_or_uuid: str,
    target_profile_name: str | None = None,
    wait_time: int | None = None,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
) -> JobOutcome:
    """Validate whether an LPM migration of an LPAR to target_system would succeed.

    lpar_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_list_lpars).
    Set wait=True to block until the validation job reaches a terminal state.

    Args:
        lpar_name_or_uuid: Source partition name or UUID.
        target_system_name_or_uuid: Destination managed-system name or UUID.
        target_profile_name: Optional destination partition profile name.
        wait_time: HMC validation wait time in seconds, or the HMC default when omitted.
        wait: Wait for the validation job's terminal outcome when true.
        timeout_seconds: Maximum client-side wait in seconds.
        poll_interval: Seconds between job-status requests while waiting.
        profile: Optional TOML profile name; uses environment defaults when omitted.
        system_name_or_uuid: Optional SystemName or UUID of the source system,
            disambiguating the partition name; when omitted the name is
            searched fleet-wide.
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
                system_name_or_uuid=system_name_or_uuid,
            )
            return result.job

    return _run(_go)


@tool(effect="destructive", operation="lpar.migrate_abort", target_kind="lpar")
def hmc_migrate_abort_lpar(
    lpar_name_or_uuid: str,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
) -> JobOutcome:
    """Abort an in-progress LPM migration of an LPAR.

    lpar_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_list_lpars).
    Returns a normalized job outcome. With wait=False, returns after submission;
    with wait=True, blocks until a terminal state or timeout.

    Args:
        lpar_name_or_uuid: Migrating partition name or UUID.
        wait: Wait for the abort job's terminal outcome when true.
        timeout_seconds: Maximum client-side wait in seconds.
        poll_interval: Seconds between job-status requests while waiting.
        profile: Optional TOML profile name; uses environment defaults when omitted.
        system_name_or_uuid: Optional SystemName or UUID of the source system,
            disambiguating the partition name; when omitted the name is
            searched fleet-wide.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            result = await abort_lpar_migration(
                hmc,
                lpar_name_or_uuid,
                wait=wait,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
                system_name_or_uuid=system_name_or_uuid,
            )
            return cast(JobOutcome, result.job)

    return _run(_go)


@tool(effect="mutate", operation="lpar.migrate_recover", target_kind="lpar")
def hmc_migrate_recover_lpar(
    lpar_name_or_uuid: str,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
) -> JobOutcome:
    """Recover an LPAR after a failed LPM migration.

    lpar_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_list_lpars).
    Returns a normalized job outcome. With wait=False, returns after submission;
    with wait=True, blocks until a terminal state or timeout.

    Args:
        lpar_name_or_uuid: Failed partition name or UUID.
        wait: Wait for the recovery job's terminal outcome when true.
        timeout_seconds: Maximum client-side wait in seconds.
        poll_interval: Seconds between job-status requests while waiting.
        profile: Optional TOML profile name; uses environment defaults when omitted.
        system_name_or_uuid: Optional SystemName or UUID of the source system,
            disambiguating the partition name; when omitted the name is
            searched fleet-wide.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            result = await recover_lpar_migration(
                hmc,
                lpar_name_or_uuid,
                wait=wait,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
                system_name_or_uuid=system_name_or_uuid,
            )
            return cast(JobOutcome, result.job)

    return _run(_go)


@tool(effect="destructive", operation="lpar.remote_restart", target_kind="lpar")
def hmc_remote_restart_lpar(
    lpar_name_or_uuid: str,
    operation: RemoteRestartOperation,
    system_name_or_uuid: str,
    target_system_name_or_uuid: str | None = None,
    use_current_data: bool = False,
    retain_devices: bool = False,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
) -> JobOutcome:
    """Remote-restart a failed LPAR on another managed system.

    lpar_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_list_lpars).
    Returns a normalized job outcome. With wait=False, returns after submission;
    with wait=True, blocks until a terminal state or timeout.

    Args:
        lpar_name_or_uuid: Failed partition name or UUID.
        operation: Explicit validate, recover, restart, cleanup, or cancel action.
        system_name_or_uuid: Source managed-system name or UUID.
        target_system_name_or_uuid: Target name or UUID; optional only for cleanup.
        use_current_data: Use current configuration data; restart only.
        retain_devices: Retain devices; cleanup only.
        wait: Wait for the remote-restart job's terminal outcome when true.
        timeout_seconds: Maximum client-side wait in seconds.
        poll_interval: Seconds between job-status requests while waiting.
        profile: Optional TOML profile name; uses environment defaults when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            result = await remote_restart_lpar(
                hmc,
                lpar_name_or_uuid,
                operation,
                system_name_or_uuid,
                target_system_name_or_uuid=target_system_name_or_uuid,
                use_current_data=use_current_data,
                retain_devices=retain_devices,
                wait=wait,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
            )
            return cast(JobOutcome, result.job)

    return _run(_go)
