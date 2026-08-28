"""MCP tools for HMC/VIOS software update and firmware update jobs."""

from __future__ import annotations

from ..tool_registry import tool_module

from typing import Any, Literal, cast

from .._app import (
    run_sync,
)

from ..client.client_factory import client_from_env
from ..operations.updates import (
    list_available_hmc_ptfs,
    update_console_software,
    update_firmware,
    update_vios,
)
from ..operations.update_models import (
    ConsoleUpdateSource,
    PlatformUpdateParameter,
    VIOSSource,
    VIOSUpdateSource,
    VIOSUpgradeSource,
)


tool, register_tools, tool_security = tool_module()


@tool(effect="destructive", operation="update.console", target_kind="console")
def hmc_update_console_software(
    console_uuid: str,
    repository: ConsoleUpdateSource,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Submit a documented HMC software update job.

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
        wait: Wait for the submitted job to reach a terminal state.
        timeout_seconds: Maximum wait duration in seconds.
        poll_interval: Seconds between job-status requests while waiting.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await update_console_software(
                hmc,
                console_uuid,
                repository,
                wait=wait,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
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

    async def _go():
        async with client_from_env(profile) as hmc:
            return await list_available_hmc_ptfs(
                hmc,
                console_uuid,
                wait=wait,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
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

    async def _go():
        async with client_from_env(profile) as hmc:
            if kind == "update":
                return await update_vios(
                    hmc,
                    vios_name_or_uuid,
                    cast(VIOSUpdateSource, repository),
                    kind,
                    wait=wait,
                    timeout_seconds=timeout_seconds,
                    poll_interval=poll_interval,
                )
            return await update_vios(
                hmc,
                vios_name_or_uuid,
                cast(VIOSUpgradeSource, repository),
                kind,
                wait=wait,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
            )

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

    async def _go():
        async with client_from_env(profile) as hmc:
            return await update_firmware(
                hmc,
                system_name_or_uuid,
                platform_update,
                wait=wait,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
            )

    return run_sync(_go)
