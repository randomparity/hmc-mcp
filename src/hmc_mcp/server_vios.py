"""MCP tools for VIOS lifecycle, NIM install, and backup/restore."""

from __future__ import annotations

from .tool_registry import tool_module

import re
import shlex
from collections.abc import Callable
from typing import Any, Literal

from ._app import (
    _run,
)

from .errors import HMCError
from .common import (
    build_config,
    client_from_env,
    is_uuid,
    resolve_lpar_uuid,
    resolve_system_uuid,
    resolve_vios_uuid,
)
from .jobs import (
    install_lpar_job,
    install_vios_job,
    validate_wait_timing,
    install_wait_timeout_seconds,
    wait_for_submitted_job,
)
from .ssh import run_hmc_cli
from .documents import LparResources, VIOS_DEFAULT_RESOURCES, build_vios_document


tool, register_tools, tool_security = tool_module()


@tool(effect="mutate", operation="vios.create", target_kind="managed_system")
def hmc_create_vios(
    system_name_or_uuid: str,
    name: str,
    resources: LparResources = VIOS_DEFAULT_RESOURCES,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Create a new Virtual IO Server (VIOS) partition on a managed system.

    system_name_or_uuid is the target managed system name or UUID.
    Memory values are in MiB; procs are shared processing units (fractional
    ok). The VIOS is created powered off with default settings — install the
    OS with hmc_install_vios before using it as a storage/network server.
    This creates a real partition — confirm its name and system before calling.

    Args:
        system_name_or_uuid: Target managed-system name or UUID.
        name: Name for the new VIOS partition.
        resources: Memory and processor settings for the VIOS.
        profile: Optional TOML profile name; uses environment defaults when omitted.
    """
    xml = build_vios_document(name=name, resources=resources)

    async def _go():
        async with client_from_env(profile) as hmc:
            system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
            return await hmc.create_logical_partition(system_uuid, xml)

    return _run(_go)


@tool(effect="destructive", operation="vios.delete", target_kind="vios")
def hmc_delete_vios(
    vios_name_or_uuid: str,
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
) -> str:
    """Delete (destroy) a VIOS partition by name or UUID.

    The VIOS must be powered off first (use hmc_power_off_vios and confirm
    with hmc_get_lpar_state). This tool refuses to
    delete a VIOS whose current state is anything other than 'not activated',
    matching the precondition check pattern used by hmc_remove_memory_pool.
    This permanently removes the VIOS and its profiles from the HMC — it is
    irreversible. Confirm the target with hmc_list_vios before calling. Returns a
    confirmation string (immediate delete — no job to poll).

    system_name_or_uuid disambiguates duplicate VIOS names; it is ignored when
    vios_name_or_uuid is already a UUID.

    Raises:
        HMCError: If the VIOS state is not 'not activated' (HTTP 409).

    Args:
        vios_name_or_uuid: VIOS partition name or UUID.
        profile: Optional TOML profile name; uses environment defaults when omitted.
        system_name_or_uuid: Optional managed system used to disambiguate a VIOS name.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            vios_uuid = await resolve_vios_uuid(
                hmc,
                vios_name_or_uuid,
                system_name_or_uuid=system_name_or_uuid,
            )
            state = await hmc.get_quick_property(
                "LogicalPartition", vios_uuid, "PartitionState"
            )
            if state != "not activated":
                raise HMCError(
                    f"Cannot delete VIOS {vios_uuid} — current state is "
                    f"{state!r}; it must be 'not activated' to delete. Power it "
                    "off (hmc_power_off_vios) and confirm with "
                    "hmc_get_lpar_state before retrying.",
                    status_code=409,
                )
            await hmc.delete_logical_partition(vios_uuid)
            return f"Deleted VIOS {vios_uuid}"

    return _run(_go)


@tool(effect="destructive", operation="vios.install", target_kind="vios")
def hmc_install_vios(
    vios_name_or_uuid: str,
    nim_ip: str,
    nim_gateway: str,
    nim_subnetmask: str,
    vios_ip: str,
    vlan_id: str = "0",
    hmc_timeout_minutes: int = 60,
    wait: bool = False,
    wait_timeout_seconds: int | None = None,
    poll_interval: int = 5,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Submit a NIM-based VIOS installation job.

    vios_name_or_uuid identifies an existing powered-off VIOS partition. The
    VIOS will PXE-boot from the NIM server at nim_ip to install its OS.
    nim_gateway and nim_subnetmask define the network for the NIM install
    boot; vios_ip is the IP address the VIOS uses during the NIM install;
    vlan_id is the VLAN tag for the install network (use "0" for untagged).
    hmc_timeout_minutes is the job timeout in minutes (default 60). Returns the submitted
    job — poll hmc_get_job for status.

    Set wait=True to block until the job reaches a terminal state.

    Args:
        vios_name_or_uuid: Powered-off VIOS partition name or UUID.
        nim_ip: IPv4 address of the NIM server.
        nim_gateway: IPv4 gateway for the installation network.
        nim_subnetmask: IPv4 subnet mask for the installation network.
        vios_ip: IPv4 address assigned to the VIOS during installation.
        vlan_id: Install-network VLAN identifier, or ``0`` for untagged traffic.
        hmc_timeout_minutes: HMC installation-job timeout in minutes.
        wait: Wait for the normalized job outcome when true.
        wait_timeout_seconds: Maximum client-side wait in seconds. When omitted,
            derives the HMC timeout in seconds plus one polling interval.
        poll_interval: Seconds between job-status requests while waiting.
        profile: Optional TOML profile name; uses environment defaults when omitted.
    """
    job_xml = install_vios_job(
        nim_ip,
        nim_gateway,
        nim_subnetmask,
        vios_ip,
        vlan_id,
        hmc_timeout_minutes=hmc_timeout_minutes,
    )
    effective_wait_timeout = install_wait_timeout_seconds(
        hmc_timeout_minutes, wait_timeout_seconds, poll_interval
    )

    async def _go():
        async with client_from_env(profile) as hmc:
            vios_uuid = await resolve_vios_uuid(hmc, vios_name_or_uuid)
            job = await hmc.submit_job(
                f"/rest/api/uom/VirtualIOServer/{vios_uuid}/do/InstallVIOS",
                job_xml,
            )
            return await wait_for_submitted_job(
                hmc, job, wait, effective_wait_timeout, poll_interval
            )

    return _run(_go)


@tool(effect="destructive", operation="lpar.install_os", target_kind="lpar")
def hmc_install_lpar_os(
    lpar_name_or_uuid: str,
    nim_ip: str,
    nim_gateway: str,
    nim_subnetmask: str,
    lpar_ip: str,
    vlan_id: str = "0",
    hmc_timeout_minutes: int = 60,
    wait: bool = False,
    wait_timeout_seconds: int | None = None,
    poll_interval: int = 5,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Submit a NIM-based LPAR OS installation job.

    lpar_name_or_uuid identifies an existing powered-off LPAR by name or UUID. The LPAR will
    PXE-boot from the NIM server at nim_ip to install its OS.
    nim_gateway and nim_subnetmask define the network for the NIM install
    boot; lpar_ip is the IP address the LPAR uses during the NIM install;
    vlan_id is the VLAN tag for the install network (use "0" for untagged).
    hmc_timeout_minutes is the job timeout in minutes (default 60). Returns the submitted
    job — poll hmc_get_job for status.

    Set wait=True to block until the job reaches a terminal state.

    Args:
        lpar_name_or_uuid: Powered-off partition name or UUID.
        nim_ip: IPv4 address of the NIM server.
        nim_gateway: IPv4 gateway for the installation network.
        nim_subnetmask: IPv4 subnet mask for the installation network.
        lpar_ip: IPv4 address assigned to the partition during installation.
        vlan_id: Install-network VLAN identifier, or ``0`` for untagged traffic.
        hmc_timeout_minutes: HMC installation-job timeout in minutes.
        wait: Wait for the normalized job outcome when true.
        wait_timeout_seconds: Maximum client-side wait in seconds. When omitted,
            derives the HMC timeout in seconds plus one polling interval.
        poll_interval: Seconds between job-status requests while waiting.
        profile: Optional TOML profile name; uses environment defaults when omitted.
    """
    job_xml = install_lpar_job(
        nim_ip,
        nim_gateway,
        nim_subnetmask,
        lpar_ip,
        vlan_id,
        hmc_timeout_minutes=hmc_timeout_minutes,
    )
    effective_wait_timeout = install_wait_timeout_seconds(
        hmc_timeout_minutes, wait_timeout_seconds, poll_interval
    )

    async def _go():
        async with client_from_env(profile) as hmc:
            lpar_uuid = await resolve_lpar_uuid(hmc, lpar_name_or_uuid)
            job = await hmc.submit_job(
                f"/rest/api/uom/LogicalPartition/{lpar_uuid}/do/InstallLPAR",
                job_xml,
            )
            return await wait_for_submitted_job(
                hmc, job, wait, effective_wait_timeout, poll_interval
            )

    return _run(_go)


BackupType = Literal["vios", "viosioconfig", "ssp"]
_VALID_BACKUP_TYPES: frozenset[BackupType] = frozenset({"vios", "viosioconfig", "ssp"})


def _parse_lsviosbackup_output(text: str) -> list[dict[str, str]]:
    """Parse ``lsviosbackup`` fixed-width table output into a list of dicts.

    The first non-empty line is the header; each subsequent non-empty line is
    a backup row. Columns are separated by two or more spaces. Rows with fewer
    values than headers are padded with empty strings so callers can tell a
    missing field from an empty one.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    headers = [h for h in re.split(r"\s{2,}", lines[0]) if h]
    results = []
    for line in lines[1:]:
        values = [v for v in re.split(r"\s{2,}", line) if v]
        row = dict(zip(headers, values))
        for header in headers[len(values) :]:
            row[header] = ""
        results.append(row)
    return results


async def _run_vios_backup_command(
    vios_name_or_uuid: str,
    build_command: Callable[[str], str],
    profile: str | None,
    system_name_or_uuid: str | None = None,
) -> str:
    if is_uuid(vios_name_or_uuid):
        vios_uuid = vios_name_or_uuid
    else:
        async with client_from_env(profile) as hmc:
            vios_uuid = await resolve_vios_uuid(
                hmc,
                vios_name_or_uuid,
                system_name_or_uuid=system_name_or_uuid,
            )
    return await run_hmc_cli(build_command(vios_uuid), build_config(profile=profile))


@tool(effect="read", operation="vios.list_backups", target_kind="vios")
def hmc_list_vios_backups(
    vios_name_or_uuid: str, profile: str | None = None
) -> list[dict[str, str]]:
    """List existing VIOS backups for a VIOS name or UUID.

    Resolves a VIOS name when needed, runs ``lsviosbackup -id <vios_uuid>``
    on the HMC via SSH, and parses the
    fixed-width table into a list of dicts keyed by the output header
    (BackupName, Date, Type).

    Args:
        vios_name_or_uuid: VIOS partition name or UUID.
        profile: Optional TOML profile name; uses environment defaults when omitted.
    """
    output = _run(
        lambda: _run_vios_backup_command(
            vios_name_or_uuid,
            lambda uuid: f"lsviosbackup -id {shlex.quote(uuid)}",
            profile,
        )
    )
    return _parse_lsviosbackup_output(output)


@tool(effect="mutate", operation="vios.backup", target_kind="vios")
def hmc_backup_vios(
    vios_name_or_uuid: str,
    backup_type: BackupType = "vios",
    profile: str | None = None,
) -> str:
    """Create a VIOS backup via the HMC CLI.

    Runs ``chviosbackup -id <vios_uuid> -operation backup -type <backup_type>``
    on the HMC via SSH. The VIOS selector accepts a partition name or UUID.

    backup_type must be one of:
      - ``vios``       — full VIOS configuration backup (default)
      - ``viosioconfig`` — I/O configuration backup
      - ``ssp``        — Shared Storage Pool (cluster) backup

    Returns the raw HMC CLI output. Poll hmc_list_vios_backups to confirm
    the backup was created.

    Args:
        vios_name_or_uuid: VIOS partition name or UUID.
        backup_type: Backup kind: vios, viosioconfig, or ssp.
        profile: Optional TOML profile name; uses environment defaults when omitted.
    """
    if backup_type not in _VALID_BACKUP_TYPES:
        raise ValueError(
            f"Invalid backup_type {backup_type!r}. "
            f"Must be one of: {', '.join(sorted(_VALID_BACKUP_TYPES))}"
        )
    return _run(
        lambda: _run_vios_backup_command(
            vios_name_or_uuid,
            lambda uuid: (
                f"chviosbackup -id {shlex.quote(uuid)} -operation backup "
                f"-type {shlex.quote(backup_type)}"
            ),
            profile,
        )
    )


def _validate_backup_name(backup_name: str) -> None:
    """Refuse a ``backup_name`` that could denote anything but a catalog entry.

    ADR 0044 keeps ``hmc_restore_vios`` bounded by the VIOS its ``-id`` selector
    names, which holds only while the value is a name resolved inside that VIOS's
    own backup catalog. Four shapes are refused, not because no catalog could hold
    such a name but because the tool cannot treat any of them as one: an empty or
    padded value, one carrying a path separator, one made only of dots, and one
    starting with ``-``. The last is refused for what is *unknown* about it — how
    the HMC CLI parses a bare leading dash in this position is not established
    here, and ``shlex.quote`` offers no cover because such a value holds no shell
    metacharacter and is emitted unquoted.

    Deliberately no character-set or length rule, so a catalog entry named outside
    whatever grammar the HMC enforces stays restorable. ADR 0039 made the same call
    for ``job_href``, refusing dot-segments but not requiring a UUID shape:
    refusing a legitimate identifier would trade a regression for reach the narrow
    refusal has already removed.
    """
    if (
        not backup_name
        or backup_name != backup_name.strip()
        or "/" in backup_name
        or "\\" in backup_name
        or backup_name.strip(".") == ""
        or backup_name.startswith("-")
    ):
        raise ValueError(
            f"backup_name {backup_name!r} must be a backup name as returned by "
            "hmc_list_vios_backups — not a path, an option, or a padded value. It "
            "is resolved inside the declared VIOS's own backup catalog."
        )


@tool(
    effect="destructive",
    operation="vios.restore",
    target_kind="vios",
    # An SSP restore can affect the cluster beyond the selected VIOS (#282).
    exhaustive_targets=False,
)
def hmc_restore_vios(
    vios_name_or_uuid: str,
    backup_name: str,
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
) -> str:
    """Restore a VIOS from a named backup via the HMC CLI.

    Runs ``chviosbackup -id <vios_uuid> -operation restore -file <backup_name>``
    on the HMC via SSH. The VIOS selector accepts a partition name or UUID;
    backup_name is the backup file name as listed by hmc_list_vios_backups.

    WARNING: Restoring overwrites the current VIOS configuration. Confirm
    the VIOS and backup_name before calling.

    system_name_or_uuid disambiguates duplicate VIOS names; it is ignored when
    vios_name_or_uuid is already a UUID.

    Returns the raw HMC CLI output.

    Args:
        vios_name_or_uuid: VIOS partition name or UUID.
        backup_name: Backup file name returned by hmc_list_vios_backups. A name
            within this VIOS's own catalog, not a path: a value that is empty or
            padded, carries a path separator, is made only of dots, or starts with
            ``-`` is refused.
        profile: Optional TOML profile name; uses environment defaults when omitted.
        system_name_or_uuid: Optional managed system used to disambiguate a VIOS name.

    Raises:
        ValueError: if ``backup_name`` could denote something outside the catalog.
    """
    _validate_backup_name(backup_name)
    return _run(
        lambda: _run_vios_backup_command(
            vios_name_or_uuid,
            lambda uuid: (
                f"chviosbackup -id {shlex.quote(uuid)} -operation restore "
                f"-file {shlex.quote(backup_name)}"
            ),
            profile,
            system_name_or_uuid,
        )
    )


@tool(effect="mutate", operation="vios.power_on", target_kind="vios")
def hmc_power_on_vios(
    vios_name_or_uuid: str,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Power on a VIOS, optionally waiting for a normalized job outcome.

    Args:
        vios_name_or_uuid: VIOS partition name or UUID.
        wait: Wait for the power job's terminal outcome when true.
        timeout_seconds: Maximum client-side wait in seconds.
        poll_interval: Seconds between job-status requests while waiting.
        profile: Optional TOML profile name; uses environment defaults when omitted.
    """

    validate_wait_timing(wait, timeout_seconds, poll_interval)

    async def _go():
        from .operations_vios import power_vios

        async with client_from_env(profile) as hmc:
            return await power_vios(
                hmc,
                vios_name_or_uuid,
                on=True,
                wait=wait,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
            )

    return _run(_go)


@tool(effect="destructive", operation="vios.power_off", target_kind="vios")
def hmc_power_off_vios(
    vios_name_or_uuid: str,
    immediate: bool = False,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
) -> dict[str, Any] | None:
    """Power off a VIOS, optionally scoped by system and waiting for completion.

    Args:
        vios_name_or_uuid: VIOS partition name or UUID.
        immediate: Request immediate shutdown instead of an orderly shutdown.
        wait: Wait for the power job's terminal outcome when true.
        timeout_seconds: Maximum client-side wait in seconds.
        poll_interval: Seconds between job-status requests while waiting.
        profile: Optional TOML profile name; uses environment defaults when omitted.
        system_name_or_uuid: Optional managed system used to disambiguate a VIOS name.
    """

    validate_wait_timing(wait, timeout_seconds, poll_interval)

    async def _go():
        from .operations_vios import power_vios

        async with client_from_env(profile) as hmc:
            return await power_vios(
                hmc,
                vios_name_or_uuid,
                on=False,
                system_name_or_uuid=system_name_or_uuid,
                immediate=immediate,
                wait=wait,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
            )

    return _run(_go)
