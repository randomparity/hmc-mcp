"""MCP tools for read-only inventory and job status, plus the HMC CLI escape hatch."""

from __future__ import annotations

import tomllib
from typing import Any

from ._app import (
    _READ_ONLY,
    _STATE_CHANGING,
    _resolve_lpar_uuid,
    _resolve_system_uuid,
    _resolve_vios_uuid,
    _run,
    mcp,
)
from .common import client_from_env, is_uuid
from .config import HMCConfig, resolve_config_path

from .ssh import run_hmc_cli


def hmc_run_command(cmd: str, profile: str | None = None) -> str:
    """Execute an arbitrary HMC CLI command over SSH and return its output.

    WARNING: This tool executes arbitrary commands on the HMC with the
    credentials configured in HMC_USER / HMC_PASSWORD (or HMC_SSH_KEY_FILE).
    It is an operator escape-hatch equivalent to Ansible ``hmc_command``.
    Use only for HMC CLI operations that have no dedicated MCP tool.

    Authentication follows the same env-var configuration as all other tools:
    set HMC_SSH_KEY_FILE to use key-based auth, otherwise password auth is used.

    profile: optional TOML profile name; when omitted the env-default HMC is used.

    Reference: https://www.ibm.com/docs/en/power10/7063-CR1?topic=hmc-commands
    """
    config = client_from_env(profile).config
    return _run(lambda: run_hmc_cli(cmd, config))


_arbitrary_command_registered = False


def register_arbitrary_command_tool() -> None:
    """Register the arbitrary-command escape hatch once for an opted-in server."""
    global _arbitrary_command_registered
    if _arbitrary_command_registered:
        return
    mcp.tool(hmc_run_command, annotations=_STATE_CHANGING)
    _arbitrary_command_registered = True


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
    profiles_raw: dict = doc.get("profiles", {})

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
def hmc_systems(
    system_name_or_uuid: str | None = None,
    state: str | None = None,
    profile: str | None = None,
) -> list[dict[str, Any]] | dict[str, Any] | None:
    """List all managed systems or get one by name or UUID.

    When system_name_or_uuid is omitted, returns a list of all managed systems
    known to the HMC — each entry has UUID, SystemName, State, MTMS (machine
    type/model/serial), IPAddress, etc.

    When system_name_or_uuid is provided, accepts either a SystemName or a UUID
    and returns the full details dict for that one system, or None if not found.

    When state is provided and system_name_or_uuid is omitted, returns only
    systems whose State property matches the given value, using the HMC
    server-side search endpoint.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            if system_name_or_uuid is None:
                if state is not None:
                    return await hmc.search_uom("ManagedSystem", "State", state)
                return await hmc.list_managed_systems()
            system_uuid = await _resolve_system_uuid(hmc, system_name_or_uuid)
            return await hmc.get_managed_system(system_uuid)

    return _run(_go)


@mcp.tool(annotations=_READ_ONLY)
def hmc_lpars(
    system_name_or_uuid: str | None = None,
    lpar_name_or_uuid: str | None = None,
    state: str | None = None,
    profile: str | None = None,
) -> list[dict[str, Any]] | dict[str, Any] | None:
    """List LPARs, get one by name or UUID, or filter by one selector.

    Supply at most one of system_name_or_uuid, lpar_name_or_uuid, and state.
    A system selector lists its LPARs, an LPAR selector returns that partition,
    and state filters the global list by PartitionState. With no selector, all
    LPARs are returned. Use hmc_get_lpar_state for a lightweight state lookup.
    """
    selectors = (system_name_or_uuid, lpar_name_or_uuid, state)
    if sum(value is not None for value in selectors) > 1:
        raise ValueError(
            "Provide at most one of system_name_or_uuid, lpar_name_or_uuid, or state"
        )

    async def _go():
        async with client_from_env(profile) as hmc:
            if lpar_name_or_uuid is not None:
                if is_uuid(lpar_name_or_uuid):
                    return await hmc.get_logical_partition(lpar_name_or_uuid)
                return await hmc.find_partition_by_name(lpar_name_or_uuid)
            if system_name_or_uuid is not None:
                system_uuid = await _resolve_system_uuid(hmc, system_name_or_uuid)
                return await hmc.list_logical_partitions(system_uuid)
            if state is not None:
                return await hmc.search_uom("LogicalPartition", "PartitionState", state)
            return await hmc.list_logical_partitions(None)

    return _run(_go)


@mcp.tool(annotations=_READ_ONLY)
def hmc_get_lpar_state(
    lpar_name_or_uuid: str,
    profile: str | None = None,
) -> str | None:
    """Return the current state of one LPAR by partition name or UUID."""

    async def _go():
        async with client_from_env(profile) as hmc:
            lpar_uuid = await _resolve_lpar_uuid(hmc, lpar_name_or_uuid)
            return await hmc.get_quick_property(
                "LogicalPartition", lpar_uuid, "PartitionState"
            )

    return _run(_go)


@mcp.tool(annotations=_READ_ONLY)
def hmc_vios(
    system_name_or_uuid: str | None = None,
    vios_name_or_uuid: str | None = None,
    state: str | None = None,
    profile: str | None = None,
) -> list[dict[str, Any]] | dict[str, Any] | None:
    """List Virtual I/O Servers or get storage-detail mappings for one.

    When vios_name_or_uuid is provided, accepts either a PartitionName or a
    UUID and returns the VIOS device mapping facts (vSCSI, NPIV, virtual
    optical) for that VIOS.

    When vios_name_or_uuid is omitted, returns a list of all VIOS entries,
    optionally restricted to one managed system via system_name_or_uuid
    (accepts either a SystemName or a UUID).

    When state is provided and vios_name_or_uuid is omitted, returns only
    VIOS entries whose PartitionState matches the given value, using the HMC
    server-side search endpoint. The state filter is ignored when
    vios_name_or_uuid or system_name_or_uuid is supplied.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            if vios_name_or_uuid is not None:
                vios_uuid = await _resolve_vios_uuid(hmc, vios_name_or_uuid)
                return await hmc.get_vios_storage_detail(vios_uuid)
            if system_name_or_uuid is not None:
                system_uuid = await _resolve_system_uuid(hmc, system_name_or_uuid)
                return await hmc.list_vios(system_uuid)
            if state is not None:
                return await hmc.search_uom("VirtualIOServer", "PartitionState", state)
            return await hmc.list_vios(None)

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
def hmc_get_job(
    job_uuid: str,
    job_href: str | None = None,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Get the status/result of an HMC job by UUID.

    *job_href* is the SELF link returned by the job-submission tool (e.g.
    hmc_power_on_lpar).  When supplied, it is used directly for the GET so
    the call works on HMC versions that do not expose Job as a root UOM
    resource type (returns HTTP 400 on those versions without this hint).
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.get_job(job_uuid, job_href=job_href)

    return _run(_go)


@mcp.tool(annotations=_READ_ONLY)
def hmc_recent_jobs(
    limit: int = 20, profile: str | None = None
) -> list[dict[str, Any]]:
    """List recent HMC jobs (most recent first, up to *limit* entries).

    Returns a list of parsed job dicts with at minimum JobID and Status.
    Useful for auditing recent HMC activity — power operations, firmware
    updates, migrations, etc.

    On HMC versions that do not expose Job as a root UOM resource type
    (HTTP 400), returns a single-element list containing an error sentinel
    dict (identified by ``"type": "error"``) rather than raising an
    exception.  Normal job dicts never carry a ``"type"`` key at the top
    level, so callers can reliably distinguish the two cases.
    """
    from .errors import HMCError

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.list_uom("Job")

    try:
        jobs = _run(_go)
    except HMCError as exc:
        if exc.status_code == 400:
            return [
                {
                    "type": "error",
                    "error": (
                        "This HMC version does not support the global Job "
                        "listing endpoint (GET /rest/api/uom/Job). "
                        "Use hmc_get_job(job_uuid, job_href=<link from "
                        "submission>) to query individual jobs."
                    ),
                    "status_code": 400,
                    "detail": str(exc),
                }
            ]
        raise
    return jobs[:limit]


def _system_capacity(
    system: dict[str, Any], lpars: list[dict[str, Any]]
) -> dict[str, Any]:
    """Compute capacity stats for one managed system from its entry + LPAR list."""
    res = system.get("Resource") or {}
    total_mem = int(res.get("AssignableSystemMemory") or 0)
    total_procs = float(res.get("ConfigurableSystemProcessorUnits") or 0.0)

    assigned_mem = 0
    assigned_procs = 0.0
    running = 0
    for lpar in lpars:
        lr = lpar.get("Resource") or {}
        assigned_mem += int(lr.get("DesiredMemory") or 0)
        try:
            assigned_procs += float(lr.get("DesiredProcessingUnits") or 0.0)
        except (TypeError, ValueError):
            pass
        if lr.get("PartitionState") == "running":
            running += 1

    return {
        "system_uuid": system.get("UUID"),
        "system_name": res.get("SystemName", ""),
        "total_memory_mb": total_mem,
        "assigned_memory_mb": assigned_mem,
        "free_memory_mb": total_mem - assigned_mem,
        "total_proc_units": total_procs,
        "assigned_proc_units": round(assigned_procs, 4),
        "free_proc_units": round(total_procs - assigned_procs, 4),
        "total_lpars": len(lpars),
        "running_lpars": running,
    }


@mcp.tool(annotations=_READ_ONLY)
def hmc_capacity_report(profile: str | None = None) -> list[dict[str, Any]]:
    """Capacity report: for each managed system, total/assigned/free memory (MiB)
    and processor units, plus running and total LPAR counts.

    Derived by listing all managed systems then fetching the LPAR list for each
    system to compute assigned resources. Free = total − assigned.
    """

    async def _go() -> list[dict[str, Any]]:
        async with client_from_env(profile) as hmc:
            systems = await hmc.list_managed_systems()
            result = []
            for system in systems:
                uuid = system.get("UUID")
                lpars = await hmc.list_logical_partitions(uuid) if uuid else []
                result.append(_system_capacity(system, lpars))
            return result

    return _run(_go)


@mcp.tool(annotations=_READ_ONLY)
def hmc_find_placement(
    desired_memory_mb: int,
    desired_proc_units: float = 0.5,
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """Find managed systems that can host a new LPAR of the given size.

    Returns systems with at least *desired_memory_mb* MiB free and at least
    *desired_proc_units* free processor units, sorted by free memory descending.
    Each result has the same fields as :func:`hmc_capacity_report`.
    """

    async def _go() -> list[dict[str, Any]]:
        async with client_from_env(profile) as hmc:
            systems = await hmc.list_managed_systems()
            candidates = []
            for system in systems:
                uuid = system.get("UUID")
                lpars = await hmc.list_logical_partitions(uuid) if uuid else []
                cap = _system_capacity(system, lpars)
                if (
                    cap["free_memory_mb"] >= desired_memory_mb
                    and cap["free_proc_units"] >= desired_proc_units
                ):
                    candidates.append(cap)
            candidates.sort(key=lambda c: c["free_memory_mb"], reverse=True)
            return candidates

    return _run(_go)


@mcp.tool(annotations=_READ_ONLY)
def hmc_find_system(name: str, profile: str | None = None) -> dict[str, Any] | None:
    """Find a managed system by its SystemName (exact match).

    Returns the full system dict if found, or None if no system with that
    name is known to the HMC.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.find_system_by_name(name)

    return _run(_go)


@mcp.tool(annotations=_READ_ONLY)
def hmc_wait_for_job(
    job_uuid: str,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    job_href: str | None = None,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Poll until COMPLETED, COMPLETED_OK, COMPLETED_WITH_ERROR, FAILED, or EXCEPTION.

    Returns the final job entry. If *timeout_seconds* elapses before a
    terminal state is reached, returns the last-seen entry regardless of
    status — check the Status field to distinguish timeout from completion.

    *job_href* is the SELF link returned by the job-submission tool.
    When supplied, polling uses that path directly so the call works on
    HMC versions that return HTTP 400 for the global Job endpoint.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.wait_for_job(
                job_uuid, timeout_seconds, poll_interval, job_href=job_href
            )

    return _run(_go)
