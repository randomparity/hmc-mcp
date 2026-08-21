"""MCP tools for VIOS lifecycle, NIM install, and backup/restore."""

from __future__ import annotations

from .tool_registry import tool_module

import csv
import shlex
from collections.abc import Callable, Mapping
from typing import Any, Literal

from ._app import (
    _run,
)

from .client import HMCClient
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
RestoreBackupType = Literal["viosioconfig", "ssp"]
_VALID_BACKUP_TYPES: frozenset[BackupType] = frozenset({"vios", "viosioconfig", "ssp"})
_VALID_RESTORE_BACKUP_TYPES: frozenset[RestoreBackupType] = frozenset(
    {"viosioconfig", "ssp"}
)


def _parse_lsviosbk_output(text: str) -> list[dict[str, str]]:
    """Parse the exact ``name,type`` CSV projection returned by ``lsviosbk``."""
    if not text.strip():
        return []
    try:
        reader = csv.DictReader(text.splitlines(), strict=True)
        if reader.fieldnames != ["name", "type"]:
            raise ValueError(
                "Malformed lsviosbk CSV: expected the exact header 'name,type'."
            )

        results: list[dict[str, str]] = []
        for row in reader:
            name = row.get("name")
            backup_type = row.get("type")
            if None in row or not name or not backup_type:
                raise ValueError(
                    "Malformed lsviosbk CSV: each row must contain exactly one "
                    "nonempty name and type."
                )
            results.append({"name": name, "type": backup_type})
    except csv.Error as exc:
        raise ValueError(f"Malformed lsviosbk CSV: {exc}") from exc
    return results


async def _resolve_vios_backup_system_name(hmc: Any, system_name_or_uuid: str) -> str:
    """Resolve a system UUID to its unique CLI MTMS identity."""
    if not is_uuid(system_name_or_uuid):
        return system_name_or_uuid

    entry = await hmc.get_managed_system(system_name_or_uuid)
    resource = (entry or {}).get("Resource") or {}
    mtms = resource.get("MachineTypeModelSerialNumber")
    if isinstance(mtms, str):
        machine_type, dash, model_and_serial = mtms.partition("-")
        model, star, serial = model_and_serial.partition("*")
        components = (machine_type, model, serial)
        if (
            dash
            and star
            and all(
                component and component == component.strip() for component in components
            )
        ):
            rendered = f"{machine_type}-{model}*{serial}"
            if rendered == mtms:
                return rendered
    elif isinstance(mtms, Mapping):
        components = (
            mtms.get("MachineType"),
            mtms.get("Model"),
            mtms.get("SerialNumber"),
        )
        if all(
            isinstance(component, str) and component.strip() for component in components
        ):
            machine_type, model, serial = components
            return f"{machine_type}-{model}*{serial}"

    raise ValueError(
        f"Managed system {system_name_or_uuid!r} has no complete, valid "
        "MachineTypeModelSerialNumber (MTMS). Use hmc_list_systems to inspect "
        "the managed system before retrying."
    )


async def _run_vios_backup_mutation_command(
    system_name_or_uuid: str,
    vios_name_or_uuid: str,
    build_command: Callable[[str, str], str],
    profile: str | None,
) -> str:
    config = build_config(profile=profile)
    system_name = system_name_or_uuid
    vios_uuid = vios_name_or_uuid
    if is_uuid(system_name_or_uuid) or not is_uuid(vios_name_or_uuid):
        async with HMCClient(config) as hmc:
            if is_uuid(system_name_or_uuid):
                system_name = await _resolve_vios_backup_system_name(
                    hmc, system_name_or_uuid
                )
            if not is_uuid(vios_name_or_uuid):
                vios_uuid = await resolve_vios_uuid(
                    hmc,
                    vios_name_or_uuid,
                    system_name_or_uuid=system_name_or_uuid,
                )
    command = build_command(system_name, vios_uuid)
    return await run_hmc_cli(command, config)


async def _run_vios_backup_list_command(
    vios_name_or_uuid: str,
    build_command: Callable[[str], str],
    profile: str | None,
) -> str:
    config = build_config(profile=profile)
    vios_uuid = vios_name_or_uuid
    if not is_uuid(vios_name_or_uuid):
        async with HMCClient(config) as hmc:
            vios_uuid = await resolve_vios_uuid(hmc, vios_name_or_uuid)
    return await run_hmc_cli(build_command(vios_uuid), config)


@tool(effect="read", operation="vios.list_backups", target_kind="vios")
def hmc_list_vios_backups(
    vios_name_or_uuid: str, profile: str | None = None
) -> list[dict[str, str]]:
    """List a VIOS backup catalog with the supported ``lsviosbk`` command.

    Resolves the required VIOS selector to a UUID, requests the explicit
    ``name,type`` CSV projection, and validates its header and every row.

    Args:
        vios_name_or_uuid: VIOS partition name or UUID.
        profile: Optional TOML profile name; uses environment defaults when omitted.

    Raises:
        ValueError: If the ``lsviosbk`` CSV is malformed.
    """
    output = _run(
        lambda: _run_vios_backup_list_command(
            vios_name_or_uuid,
            lambda uuid: (
                f"lsviosbk --filter {shlex.quote(f'vios_uuids={uuid}')} "
                "-F name,type --header"
            ),
            profile,
        )
    )
    return _parse_lsviosbk_output(output)


@tool(effect="mutate", operation="vios.backup", target_kind="vios")
def hmc_backup_vios(
    system_name_or_uuid: str,
    vios_name_or_uuid: str,
    backup_name: str,
    backup_type: BackupType = "vios",
    profile: str | None = None,
) -> str:
    """Create a named VIOS backup with the supported ``mkviosbk`` command.

    Both managed-system and VIOS selectors are required. ``backup_type`` is
    limited to ``vios``, ``viosioconfig``, or ``ssp`` and defaults to ``vios``.
    The backup name must identify one catalog entry, not a path or option.
    Returns the raw HMC CLI output.

    Args:
        system_name_or_uuid: Managed system name or UUID. UUIDs resolve to MTMS.
        vios_name_or_uuid: VIOS partition name or UUID.
        backup_name: Name for the new backup catalog entry.
        backup_type: Backup kind: vios, viosioconfig, or ssp.
        profile: Optional TOML profile name; uses environment defaults when omitted.

    Raises:
        ValueError: If the backup type or catalog name is invalid, or a selector
            cannot be resolved to the required CLI identity.
    """
    if backup_type not in _VALID_BACKUP_TYPES:
        raise ValueError(
            f"Invalid backup_type {backup_type!r}. "
            f"Must be one of: {', '.join(sorted(_VALID_BACKUP_TYPES))}"
        )
    _validate_backup_name(backup_name)
    return _run(
        lambda: _run_vios_backup_mutation_command(
            system_name_or_uuid,
            vios_name_or_uuid,
            lambda system_name, vios_uuid: (
                f"mkviosbk -t {shlex.quote(backup_type)} "
                f"-m {shlex.quote(system_name)} --uuid {shlex.quote(vios_uuid)} "
                f"-f {shlex.quote(backup_name)}"
            ),
            profile,
        )
    )


def _validate_backup_name(backup_name: str) -> None:
    """Refuse a ``backup_name`` that could denote anything but a catalog entry.

    ADR 0044 keeps catalog operations bounded by the VIOS their ``--uuid``
    selector names, which holds only while this value is resolved inside that
    VIOS's own backup catalog. Four shapes are refused, not because no catalog
    could hold such a name but because the tool cannot treat any of them as one:
    an empty or padded value, one carrying a path separator, one made only of
    dots, and one starting with ``-``. The last is refused for what is *unknown*
    about it — how the HMC CLI parses a bare leading dash in this position is not
    established here, and ``shlex.quote`` offers no cover because such a value
    holds no shell metacharacter and is emitted unquoted.

    Deliberately no character-set or length rule, so a catalog entry outside
    whatever grammar the HMC enforces stays usable. ADR 0039 made the same call
    for ``job_href``, refusing dot-segments but not requiring a UUID shape:
    refusing a legitimate identifier would trade a regression for reach the
    narrow refusal has already removed.
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
            f"backup_name {backup_name!r} must be a nonempty, unpadded catalog "
            "name without path separators; it must not consist only of dots or "
            "start with '-'. It is resolved inside the declared VIOS's own backup "
            "catalog."
        )


@tool(
    effect="destructive",
    operation="vios.restore",
    target_kind="vios",
    # An SSP restore can affect the cluster beyond the selected VIOS (#282).
    exhaustive_targets=False,
)
def hmc_restore_vios(
    system_name_or_uuid: str,
    vios_name_or_uuid: str,
    backup_name: str,
    backup_type: RestoreBackupType,
    restart_if_required: bool = False,
    profile: str | None = None,
) -> str:
    """Restore a catalog entry with the supported ``rstviosbk`` command.

    Both managed-system and VIOS selectors are required. ``backup_type`` must
    be ``viosioconfig`` or ``ssp``; full-image ``vios`` restore is unsupported.
    When ``restart_if_required`` is true, ``-r`` authorizes a VIOS restart only
    after a failed restore attempt. The catalog name must not be a path, option,
    empty, or padded value. Returns the raw HMC CLI output.

    Args:
        system_name_or_uuid: Managed system name or UUID. UUIDs resolve to MTMS.
        vios_name_or_uuid: VIOS partition name or UUID.
        backup_name: Backup catalog name returned by hmc_list_vios_backups.
        backup_type: Restore kind: viosioconfig or ssp.
        restart_if_required: Append ``-r`` to authorize a conditional restart.
        profile: Optional TOML profile name; uses environment defaults when omitted.

    Raises:
        ValueError: If the restore type or catalog name is invalid, or a selector
            cannot be resolved to the required CLI identity.
    """
    if backup_type not in _VALID_RESTORE_BACKUP_TYPES:
        raise ValueError(
            f"Invalid backup_type {backup_type!r}. "
            f"Must be one of: {', '.join(sorted(_VALID_RESTORE_BACKUP_TYPES))}"
        )
    _validate_backup_name(backup_name)
    return _run(
        lambda: _run_vios_backup_mutation_command(
            system_name_or_uuid,
            vios_name_or_uuid,
            lambda system_name, vios_uuid: (
                f"rstviosbk -t {shlex.quote(backup_type)} "
                f"-m {shlex.quote(system_name)} --uuid {shlex.quote(vios_uuid)} "
                f"-f {shlex.quote(backup_name)}"
                f"{' -r' if restart_if_required else ''}"
            ),
            profile,
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
