"""MCP tools for managed-system inventory and lifecycle operations."""

from __future__ import annotations

from ..tool_registry import tool_module

from typing import Any

from .._app import (
    run_sync,
    run_limited_collection,
)
from ..client.client_factory import client_from_env
from ..resource_identity import (
    resolve_system_uuid,
    resolve_vios_uuid,
)
from ..config import (
    config_inventory,
)
from ..documents import (
    MemoryMirroringMode,
    PowerOffPolicy,
    PowerOnLparStartPolicy,
)
from ..operations.systems import (
    ManagedSystemState,
    get_system,
    list_systems,
    modify_system,
    power_system,
)
from ..operations.lpar.core import (
    PartitionState,
    get_lpar,
    get_lpar_state,
    list_lpars,
)


tool, register_tools, tool_security = tool_module()


@tool(effect="read", operation="console.info", target_kind="console")
def hmc_console_info(profile: str | None = None) -> dict[str, Any] | None:
    """Get HMC version, network configuration and links to managed systems.

    Useful as a connectivity check — this is the cheapest HMC call.

    Args:
        profile: Optional configured HMC profile name; uses the default when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.get_console_info()

    return run_sync(_go)


@tool(
    effect="read",
    operation="config.list_hosts",
    target_kind="none",
    connection_argument=None,
)
def hmc_list_configured_hosts() -> dict[str, Any]:
    """List all configured HMC profiles from the platform-native TOML config.

    Returns profile names, hostnames, users, ports, TLS settings, default
    status, and credential-presence booleans. Never returns passwords, resolved
    password_env values, or SSH key contents — only has_password / has_ssh_key
    presence indicators.

    No network calls are made. When no config file exists, returns an empty
    profile list.
    """
    return config_inventory()


@tool(effect="read", operation="system.list", target_kind="console")
def hmc_list_systems(
    state: ManagedSystemState | None = None,
    profile: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """List managed systems, optionally filtered by state.

    When state is omitted, returns all managed systems known to the HMC. Each
    entry has UUID, SystemName, State, MTMS (machine type/model/serial),
    IPAddress, etc.

    When state is provided, returns only systems whose State property matches
    the given value, using the HMC server-side search endpoint. Use
    hmc_get_system for a single system lookup by name.

    Args:
        state: Optional exact managed-system State value to filter server-side.
        profile: Optional configured HMC profile name; uses the default when omitted.
        limit: Maximum entries returned after the complete HMC feed is transferred
            and parsed; omitted returns all entries. This client-side cap does not
            reduce HMC work or network transfer.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await list_systems(hmc, state)

    return run_limited_collection(_go, limit)


@tool(effect="read", operation="lpar.list", target_kind="managed_system")
def hmc_list_lpars(
    system_name_or_uuid: str | None = None,
    state: PartitionState | None = None,
    profile: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """List LPARs, optionally filtered by system or state.

    Supply at most one of system_name_or_uuid and state. Use hmc_get_lpar for a
    single partition or hmc_get_lpar_state for a lightweight state lookup.

    Args:
        system_name_or_uuid: Optional SystemName or UUID whose partitions to list.
        state: Optional exact PartitionState value to filter server-side.
        profile: Optional configured HMC profile name; uses the default when omitted.
        limit: Maximum entries returned after the complete HMC feed is transferred
            and parsed; omitted returns all entries. This client-side cap does not
            reduce HMC work or network transfer.
    """
    async def _go():
        async with client_from_env(profile) as hmc:
            return await list_lpars(hmc, system_name_or_uuid, state)

    return run_limited_collection(_go, limit)


@tool(effect="read", operation="lpar.get", target_kind="lpar")
def hmc_get_lpar(
    lpar_name_or_uuid: str,
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
) -> dict[str, Any] | None:
    """Get one logical partition by partition name or UUID.

    Args:
        lpar_name_or_uuid: PartitionName or UUID of the logical partition.
        profile: Optional configured HMC profile name; uses the default when omitted.
        system_name_or_uuid: Optional SystemName or UUID that disambiguates the
            partition name; when omitted the name is searched fleet-wide.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await get_lpar(
                hmc,
                lpar_name_or_uuid,
                system_name_or_uuid=system_name_or_uuid,
            )

    return run_sync(_go)


@tool(effect="read", operation="lpar.get_state", target_kind="lpar")
def hmc_get_lpar_state(
    lpar_name_or_uuid: str,
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
) -> str | None:
    """Return the current state of one LPAR by partition name or UUID.

    Args:
        lpar_name_or_uuid: PartitionName or UUID of the logical partition.
        profile: Optional configured HMC profile name; uses the default when omitted.
        system_name_or_uuid: Optional SystemName or UUID that disambiguates the
            partition name; when omitted the name is searched fleet-wide.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await get_lpar_state(
                hmc,
                lpar_name_or_uuid,
                system_name_or_uuid=system_name_or_uuid,
            )

    return run_sync(_go)


@tool(effect="read", operation="vios.list", target_kind="managed_system")
def hmc_list_vios(
    system_name_or_uuid: str | None = None,
    state: PartitionState | None = None,
    profile: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """List Virtual I/O Servers, optionally filtered by system or state.

    Results may be restricted to one managed system via system_name_or_uuid
    (accepts either a SystemName or a UUID).

    When state is provided, returns only
    VIOS entries whose PartitionState matches the given value, using the HMC
    server-side search endpoint. Supply at most one selector. Use hmc_get_vios
    for the storage-detail mappings of one VIOS.

    Args:
        system_name_or_uuid: Optional SystemName or UUID whose VIOSes to list.
        state: Optional exact PartitionState value to filter server-side.
        profile: Optional configured HMC profile name; uses the default when omitted.
        limit: Maximum entries returned after the complete HMC feed is transferred
            and parsed; omitted returns all entries. This client-side cap does not
            reduce HMC work or network transfer.
    """
    if system_name_or_uuid is not None and state is not None:
        raise ValueError("Provide at most one of system_name_or_uuid or state")

    async def _go():
        async with client_from_env(profile) as hmc:
            if system_name_or_uuid is not None:
                system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
                return await hmc.list_vios(system_uuid)
            if state is not None:
                return await hmc.search_uom("VirtualIOServer", "PartitionState", state)
            return await hmc.list_vios(None)

    return run_limited_collection(_go, limit)


@tool(effect="read", operation="vios.get", target_kind="vios")
def hmc_get_vios(
    vios_name_or_uuid: str, profile: str | None = None
) -> dict[str, Any] | None:
    """Get storage-detail mappings for one VIOS by partition name or UUID.

    Args:
        vios_name_or_uuid: PartitionName or UUID of the Virtual I/O Server.
        profile: Optional configured HMC profile name; uses the default when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            vios_uuid = await resolve_vios_uuid(hmc, vios_name_or_uuid)
            return await hmc.get_vios_storage_detail(vios_uuid)

    return run_sync(_go)


@tool(effect="read", operation="console.list_resources", target_kind="console")
def hmc_list_resources(
    resource_type: str,
    profile: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """List any uom resource type exposed by the HMC.

    Examples: ManagedSystem, LogicalPartition, VirtualIOServer,
    LogicalPartitionProfile, VirtualSwitch, VirtualNetwork, SharedMemoryPool,
    SharedProcessorPool, HostEthernetAdapter, SRIOVAdapter, Cluster.

    Args:
        resource_type: Exact HMC UOM resource type to list.
        profile: Optional configured HMC profile name; uses the default when omitted.
        limit: Maximum entries returned after the complete HMC feed is transferred
            and parsed; omitted returns all entries. This client-side cap does not
            reduce HMC work or network transfer.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.list_uom(resource_type)

    return run_limited_collection(_go, limit)


@tool(effect="read", operation="system.get", target_kind="managed_system")
def hmc_get_system(
    system_name_or_uuid: str, profile: str | None = None
) -> dict[str, Any] | None:
    """Get a managed system by exact SystemName or UUID.

    Returns the full system dict if found, or None if no system with that
    name is known to the HMC.

    Args:
        system_name_or_uuid: Exact SystemName or UUID of the managed system.
        profile: Optional configured HMC profile name; uses the default when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await get_system(hmc, system_name_or_uuid)

    return run_sync(_go)


@tool(effect="mutate", operation="system.modify", target_kind="managed_system")
def hmc_modify_system(
    system_name_or_uuid: str,
    new_name: str | None = None,
    power_off_policy: PowerOffPolicy | None = None,
    power_on_lpar_start_policy: PowerOnLparStartPolicy | None = None,
    pend_mem_region_size: int | None = None,
    requested_num_sys_huge_pages: int | None = None,
    mem_mirroring_mode: MemoryMirroringMode | None = None,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Modify a managed system's configuration, leaving omitted fields unchanged.

    Args:
        system_name_or_uuid: SystemName or UUID of the managed system to modify.
        new_name: Replacement SystemName, or null to leave it unchanged.
        power_off_policy: Power-off policy, or null to leave it unchanged.
        power_on_lpar_start_policy: LPAR auto-start policy, or null to leave unchanged.
        pend_mem_region_size: Pending memory-region size in MiB, or null for unchanged.
        requested_num_sys_huge_pages: Requested system huge-page count, or null.
        mem_mirroring_mode: Memory-mirroring mode, or null to leave it unchanged.
        profile: Optional configured HMC profile name; uses the default when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await modify_system(
                hmc,
                system_name_or_uuid,
                new_name=new_name,
                power_off_policy=power_off_policy,
                power_on_lpar_start_policy=power_on_lpar_start_policy,
                pend_mem_region_size=pend_mem_region_size,
                requested_num_sys_huge_pages=requested_num_sys_huge_pages,
                mem_mirroring_mode=mem_mirroring_mode,
            )

    return run_sync(_go)


@tool(effect="mutate", operation="system.power_on", target_kind="managed_system")
def hmc_power_on_system(
    system_name_or_uuid: str,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Power on a managed system, optionally waiting for a terminal job state.

    Returns the submitted job immediately by default. With ``wait=true``,
    returns the last polled job after it reaches a terminal state or the timeout.

    Args:
        system_name_or_uuid: SystemName or UUID of the managed system to power on.
        wait: Whether to poll the submitted job until terminal or timed out.
        timeout_seconds: Maximum polling duration in seconds when waiting.
        poll_interval: Seconds between job polls when waiting; must be positive.
        profile: Optional configured HMC profile name; uses the default when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await power_system(
                hmc,
                system_name_or_uuid,
                on=True,
                wait=wait,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
            )

    return run_sync(_go)


@tool(effect="destructive", operation="system.power_off", target_kind="managed_system")
def hmc_power_off_system(
    system_name_or_uuid: str,
    immediate: bool = False,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Power off a managed system, optionally waiting for a terminal job state.

    Returns the submitted job immediately by default. With ``wait=true``,
    returns the last polled job after it reaches a terminal state or the timeout.

    Args:
        system_name_or_uuid: SystemName or UUID of the managed system to power off.
        immediate: Whether to request immediate shutdown instead of a graceful shutdown.
        wait: Whether to poll the submitted job until terminal or timed out.
        timeout_seconds: Maximum polling duration in seconds when waiting.
        poll_interval: Seconds between job polls when waiting; must be positive.
        profile: Optional configured HMC profile name; uses the default when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await power_system(
                hmc,
                system_name_or_uuid,
                on=False,
                immediate=immediate,
                wait=wait,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
            )

    return run_sync(_go)
