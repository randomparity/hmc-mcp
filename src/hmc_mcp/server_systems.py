"""MCP tools for managed-system inventory and lifecycle operations."""

from __future__ import annotations

import tomllib
from typing import Any

from ._app import (
    _DESTRUCTIVE,
    _READ_ONLY,
    _run,
    mcp,
)
from .common import (
    client_from_env,
    is_uuid,
    resolve_lpar_uuid,
    resolve_system_uuid,
    resolve_vios_uuid,
)
from .config import HMCConfig, resolve_config_path
from .documents import (
    MemoryMirroringMode,
    PowerOffPolicy,
    PowerOnLparStartPolicy,
    build_managed_system_document,
)
from .jobs import validate_wait_timing, wait_for_submitted_job


@mcp.tool(annotations=_READ_ONLY)
def hmc_console_info(profile: str | None = None) -> dict[str, Any] | None:
    """Get HMC version, network configuration and links to managed systems.

    Useful as a connectivity check — this is the cheapest HMC call.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.get_console_info()

    return _run(_go)


@mcp.tool(annotations=_READ_ONLY)
def hmc_list_configured_hosts() -> dict[str, Any]:
    """List all configured HMC profiles from the platform-native TOML config.

    Returns profile names, hostnames, users, ports, TLS settings, default
    status, and credential-presence booleans. Never returns passwords, resolved
    password_env values, or SSH key contents — only has_password / has_ssh_key
    presence indicators.

    No network calls are made. When no config file exists, returns an empty
    profile list.
    """
    config_path = resolve_config_path()
    if config_path is None:
        return {"profiles": [], "config_file": None}

    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"{config_path}: cannot read config file: {exc}") from exc

    try:
        doc = tomllib.loads(raw_text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{config_path}: TOML parse error: {exc}") from exc

    default_profile = doc.get("default_profile")
    profiles_raw: dict[str, Any] = doc.get("profiles", {})

    # Read the HMCConfig field defaults once — port and verify_ssl come from
    # the model, not hardcoded constants, so they stay in sync if the model changes.
    _fields = HMCConfig.model_fields
    _default_port = int(_fields["port"].default)
    _default_verify_ssl = bool(_fields["verify_ssl"].default)

    profiles = []
    for name, entry in profiles_raw.items():
        if not isinstance(entry, dict):
            raise ValueError(
                f"{config_path}: profile {name!r} must be a TOML table, "
                f"got {type(entry).__name__}"
            )
        # Build each profile dict from named fields only.
        # NEVER spread entry directly — it may contain a literal "password" key.
        # Use key-presence ('in') for credential booleans — truthiness would give
        # False for password = "" which is present-but-empty, diverging from
        # load_profile()'s "key" in entry check.
        profiles.append(
            {
                "name": name,
                "host": entry.get("host", ""),
                "user": entry.get("user", ""),
                "port": int(entry.get("port", _default_port)),
                "verify_ssl": bool(entry.get("verify_ssl", _default_verify_ssl)),
                "is_default": (name == default_profile),
                "has_password": ("password" in entry or "password_env" in entry),
                "has_ssh_key": ("ssh_key_file" in entry),
            }
        )

    return {"profiles": profiles, "config_file": str(config_path)}


@mcp.tool(annotations=_READ_ONLY)
def hmc_list_systems(
    state: str | None = None,
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """List managed systems, optionally filtered by state.

    When state is omitted, returns all managed systems known to the HMC. Each
    entry has UUID, SystemName, State, MTMS (machine type/model/serial),
    IPAddress, etc.

    When state is provided, returns only systems whose State property matches
    the given value, using the HMC server-side search endpoint. Use
    hmc_get_system for a single system lookup by name.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            if state is not None:
                return await hmc.search_uom("ManagedSystem", "State", state)
            return await hmc.list_managed_systems()

    return _run(_go)


@mcp.tool(annotations=_READ_ONLY)
def hmc_list_lpars(
    system_name_or_uuid: str | None = None,
    state: str | None = None,
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """List LPARs, optionally filtered by system or state.

    Supply at most one of system_name_or_uuid and state. Use hmc_get_lpar for a
    single partition or hmc_get_lpar_state for a lightweight state lookup.
    """
    if system_name_or_uuid is not None and state is not None:
        raise ValueError("Provide at most one of system_name_or_uuid or state")

    async def _go():
        async with client_from_env(profile) as hmc:
            if system_name_or_uuid is not None:
                system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
                return await hmc.list_logical_partitions(system_uuid)
            if state is not None:
                return await hmc.search_uom("LogicalPartition", "PartitionState", state)
            return await hmc.list_logical_partitions(None)

    return _run(_go)


@mcp.tool(annotations=_READ_ONLY)
def hmc_get_lpar(
    lpar_name_or_uuid: str, profile: str | None = None
) -> dict[str, Any] | None:
    """Get one logical partition by partition name or UUID."""

    async def _go():
        async with client_from_env(profile) as hmc:
            if is_uuid(lpar_name_or_uuid):
                return await hmc.get_logical_partition(lpar_name_or_uuid)
            return await hmc.find_partition_by_name(lpar_name_or_uuid)

    return _run(_go)


@mcp.tool(annotations=_READ_ONLY)
def hmc_get_lpar_state(
    lpar_name_or_uuid: str,
    profile: str | None = None,
) -> str | None:
    """Return the current state of one LPAR by partition name or UUID."""

    async def _go():
        async with client_from_env(profile) as hmc:
            lpar_uuid = await resolve_lpar_uuid(hmc, lpar_name_or_uuid)
            return await hmc.get_quick_property(
                "LogicalPartition", lpar_uuid, "PartitionState"
            )

    return _run(_go)


@mcp.tool(annotations=_READ_ONLY)
def hmc_list_vios(
    system_name_or_uuid: str | None = None,
    state: str | None = None,
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """List Virtual I/O Servers, optionally filtered by system or state.

    Results may be restricted to one managed system via system_name_or_uuid
    (accepts either a SystemName or a UUID).

    When state is provided, returns only
    VIOS entries whose PartitionState matches the given value, using the HMC
    server-side search endpoint. Supply at most one selector. Use hmc_get_vios
    for the storage-detail mappings of one VIOS.
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

    return _run(_go)


@mcp.tool(annotations=_READ_ONLY)
def hmc_get_vios(
    vios_name_or_uuid: str, profile: str | None = None
) -> dict[str, Any] | None:
    """Get storage-detail mappings for one VIOS by partition name or UUID."""

    async def _go():
        async with client_from_env(profile) as hmc:
            vios_uuid = await resolve_vios_uuid(hmc, vios_name_or_uuid)
            return await hmc.get_vios_storage_detail(vios_uuid)

    return _run(_go)


@mcp.tool(annotations=_READ_ONLY)
def hmc_list_resources(
    resource_type: str, profile: str | None = None
) -> list[dict[str, Any]]:
    """List any uom resource type exposed by the HMC.

    Examples: ManagedSystem, LogicalPartition, VirtualIOServer,
    LogicalPartitionProfile, VirtualSwitch, VirtualNetwork, SharedMemoryPool,
    SharedProcessorPool, HostEthernetAdapter, SRIOVAdapter, Cluster.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.list_uom(resource_type)

    return _run(_go)


@mcp.tool(annotations=_READ_ONLY)
def hmc_get_system(
    system_name_or_uuid: str, profile: str | None = None
) -> dict[str, Any] | None:
    """Get a managed system by exact SystemName or UUID.

    Returns the full system dict if found, or None if no system with that
    name is known to the HMC.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            if is_uuid(system_name_or_uuid):
                return await hmc.get_managed_system(system_name_or_uuid)
            return await hmc.find_system_by_name(system_name_or_uuid)

    return _run(_go)


@mcp.tool
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
    """Modify a managed system's configuration, leaving omitted fields unchanged."""
    xml = build_managed_system_document(
        new_name=new_name,
        power_off_policy=power_off_policy,
        power_on_lpar_start_policy=power_on_lpar_start_policy,
        pend_mem_region_size=pend_mem_region_size,
        requested_num_sys_huge_pages=requested_num_sys_huge_pages,
        mem_mirroring_mode=mem_mirroring_mode,
    )

    async def _go():
        async with client_from_env(profile) as hmc:
            system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
            return await hmc.modify_managed_system(system_uuid, xml)

    return _run(_go)


@mcp.tool
def hmc_power_on_system(
    system_name_or_uuid: str,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Power on a managed system, optionally waiting for a terminal job state."""

    validate_wait_timing(wait, timeout_seconds, poll_interval)

    async def _go():
        async with client_from_env(profile) as hmc:
            system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
            job = await hmc.power_on_system(system_uuid)
            return await wait_for_submitted_job(
                hmc, job, wait, timeout_seconds, poll_interval
            )

    return _run(_go)


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_power_off_system(
    system_name_or_uuid: str,
    immediate: bool = False,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Power off a managed system, optionally waiting for a terminal job state."""

    validate_wait_timing(wait, timeout_seconds, poll_interval)

    async def _go():
        async with client_from_env(profile) as hmc:
            system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
            job = await hmc.power_off_system(system_uuid, immediate)
            return await wait_for_submitted_job(
                hmc, job, wait, timeout_seconds, poll_interval
            )

    return _run(_go)
