"""Presentation-neutral VIOS operations."""

from __future__ import annotations

import csv
import io
import shlex
from collections.abc import Mapping
from typing import Any, Literal

from hmc_mcp.client.core import HMCClient
from hmc_mcp.operations.partition_state import PARTITION_STATES, PartitionState

from ..documents import LparResources, build_vios_document
from ..errors import HMCError
from ..jobs import (
    DEFAULT_JOB_POLL_INTERVAL,
    DEFAULT_JOB_TIMEOUT_SECONDS,
    validate_wait_timing,
    wait_for_submitted_job,
)
from ..resource_identity import is_uuid, resolve_system_uuid, resolve_vios_uuid
from ..ssh.commands import build_filter
from ..ssh.transport import run_hmc_cli


async def list_vios(
    hmc: HMCClient,
    system_name_or_uuid: str | None = None,
    state: PartitionState | None = None,
) -> list[dict[str, Any]]:
    """List VIOSes, optionally scoped to one system or partition state."""
    if system_name_or_uuid is not None and state is not None:
        raise ValueError("Provide at most one of system_name_or_uuid or state")
    if state is not None and state not in PARTITION_STATES:
        allowed = ", ".join(sorted(PARTITION_STATES))
        raise ValueError(f"state must be one of: {allowed}")
    system_uuid = (
        await resolve_system_uuid(hmc, system_name_or_uuid)
        if system_name_or_uuid is not None
        else None
    )
    vios = (
        await hmc.search_uom("VirtualIOServer", "PartitionState", state)
        if system_uuid is None and state is not None
        else await hmc.list_vios(system_uuid)
    )
    if state is None or system_uuid is None:
        return vios
    return [
        entry
        for entry in vios
        if (entry.get("Resource") or {}).get("PartitionState") == state
    ]


async def get_vios(
    hmc: HMCClient,
    vios_name_or_uuid: str,
    *,
    system_name_or_uuid: str | None = None,
) -> dict[str, Any] | None:
    """Get VIOS storage detail by UUID or an optionally system-scoped name.

    System scope disambiguates duplicate VIOS names; UUID selectors do not
    require it.
    """
    vios_uuid = await resolve_vios_uuid(
        hmc, vios_name_or_uuid, system_name_or_uuid=system_name_or_uuid
    )
    return await hmc.get_vios_storage_detail(vios_uuid)


async def create_vios(
    hmc: HMCClient,
    system_name_or_uuid: str,
    name: str,
    resources: LparResources,
) -> dict[str, Any] | None:
    """Create a VIOS partition on a managed system."""
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    return await hmc.create_logical_partition(
        system_uuid, build_vios_document(name=name, resources=resources)
    )


async def delete_vios(
    hmc: HMCClient,
    vios_name_or_uuid: str,
    *,
    system_name_or_uuid: str | None = None,
) -> str:
    """Delete an inactive VIOS partition.

    Raises:
        HMCError: If the VIOS is not in the ``not activated`` state.
    """
    vios_uuid = await resolve_vios_uuid(
        hmc, vios_name_or_uuid, system_name_or_uuid=system_name_or_uuid
    )
    state = await hmc.get_quick_property(
        "LogicalPartition", vios_uuid, "PartitionState"
    )
    if state != "not activated":
        raise HMCError(
            f"Cannot delete VIOS {vios_uuid} — current state is {state!r}; it "
            "must be 'not activated' to delete. Power it off "
            "(hmc_power_off_vios) and confirm with hmc_get_lpar_state before retrying.",
            status_code=409,
        )
    await hmc.delete_logical_partition(vios_uuid)
    return vios_uuid


async def power_vios(
    hmc: HMCClient,
    vios_name_or_uuid: str,
    *,
    system_name_or_uuid: str | None = None,
    power_on: bool,
    immediate: bool = False,
    wait: bool = False,
    timeout_seconds: int = DEFAULT_JOB_TIMEOUT_SECONDS,
    poll_interval: int = DEFAULT_JOB_POLL_INTERVAL,
) -> dict[str, Any] | None:
    """Submit a VIOS power job and optionally wait for completion.

    Raises:
        ValueError: If the polling controls are invalid.
    """
    validate_wait_timing(wait, timeout_seconds, poll_interval)
    vios_uuid = await resolve_vios_uuid(
        hmc, vios_name_or_uuid, system_name_or_uuid=system_name_or_uuid
    )
    if power_on:
        job = await hmc.power_on_vios(vios_uuid)
    else:
        job = await hmc.power_off_vios(vios_uuid, immediate=immediate)
    return await wait_for_submitted_job(hmc, job, wait, timeout_seconds, poll_interval)


async def _resolve_vios_backup_system_name(
    hmc: HMCClient, system_name_or_uuid: str
) -> str:
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
        if dash and star and all(part and part == part.strip() for part in components):
            rendered = f"{machine_type}-{model}*{serial}"
            if rendered == mtms:
                return rendered
    elif isinstance(mtms, Mapping):
        components = (
            mtms.get("MachineType"),
            mtms.get("Model"),
            mtms.get("SerialNumber"),
        )
        if all(isinstance(part, str) and part.strip() for part in components):
            machine_type, model, serial = components
            return f"{machine_type}-{model}*{serial}"
    raise ValueError(
        f"Managed system {system_name_or_uuid!r} has no complete, valid "
        "MachineTypeModelSerialNumber (MTMS). Use hmc_list_systems to inspect "
        "the managed system before retrying."
    )


BackupType = Literal["vios", "viosioconfig", "ssp"]
RestoreBackupType = Literal["viosioconfig", "ssp"]
_VALID_BACKUP_TYPES: frozenset[BackupType] = frozenset({"vios", "viosioconfig", "ssp"})
_VALID_RESTORE_BACKUP_TYPES: frozenset[RestoreBackupType] = frozenset(
    {"viosioconfig", "ssp"}
)


async def _resolve_vios_backup_selectors(
    hmc: HMCClient,
    system_name_or_uuid: str,
    vios_name_or_uuid: str,
) -> tuple[str, str]:
    """Resolve backup selectors to the identities required by the HMC CLI."""
    system_name = system_name_or_uuid
    vios_uuid = vios_name_or_uuid
    if is_uuid(system_name_or_uuid) or not is_uuid(vios_name_or_uuid):
        if is_uuid(system_name_or_uuid):
            system_name = await _resolve_vios_backup_system_name(
                hmc, system_name_or_uuid
            )
        if not is_uuid(vios_name_or_uuid):
            vios_uuid = await resolve_vios_uuid(
                hmc, vios_name_or_uuid, system_name_or_uuid=system_name_or_uuid
            )
    return system_name, vios_uuid


async def list_vios_backups(
    hmc: HMCClient,
    vios_name_or_uuid: str,
    *,
    system_name_or_uuid: str | None = None,
) -> list[dict[str, str]]:
    """Return the validated backup catalog for one VIOS."""
    vios_uuid = vios_name_or_uuid
    if not is_uuid(vios_name_or_uuid):
        vios_uuid = await resolve_vios_uuid(
            hmc, vios_name_or_uuid, system_name_or_uuid=system_name_or_uuid
        )
    command = (
        f"lsviosbk --filter {shlex.quote(build_filter([('vios_uuids', vios_uuid)]))} "
        "-F name,type --header"
    )
    output = await run_hmc_cli(command, hmc.config)
    if not output.strip():
        return []
    try:
        reader = csv.DictReader(io.StringIO(output, newline=""), strict=True)
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


def validate_vios_backup_name(backup_name: str) -> None:
    """Refuse a name that could identify anything except one catalog entry.

    Catalog operations stay bounded by the VIOS selected with ``--uuid`` only
    while the backup name cannot be interpreted as a path or command option.
    Deliberately avoid a character-set or length rule so valid names outside a
    guessed HMC grammar remain usable.
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


def validate_vios_backup_request(backup_name: str, backup_type: BackupType) -> None:
    """Validate a backup request before opening any HMC connection."""
    if backup_type not in _VALID_BACKUP_TYPES:
        raise ValueError(
            f"Invalid backup_type {backup_type!r}. "
            f"Must be one of: {', '.join(sorted(_VALID_BACKUP_TYPES))}"
        )
    validate_vios_backup_name(backup_name)


def validate_vios_restore_request(
    backup_name: str, backup_type: RestoreBackupType
) -> None:
    """Validate a restore request before opening any HMC connection."""
    if backup_type not in _VALID_RESTORE_BACKUP_TYPES:
        raise ValueError(
            f"Invalid backup_type {backup_type!r}. "
            f"Must be one of: {', '.join(sorted(_VALID_RESTORE_BACKUP_TYPES))}"
        )
    validate_vios_backup_name(backup_name)


async def backup_vios(
    hmc: HMCClient,
    vios_name_or_uuid: str,
    *,
    system_name_or_uuid: str,
    backup_name: str,
    backup_type: BackupType = "vios",
) -> str:
    """Create a named VIOS backup and return the raw HMC CLI output."""
    validate_vios_backup_request(backup_name, backup_type)
    system_name, vios_uuid = await _resolve_vios_backup_selectors(
        hmc, system_name_or_uuid, vios_name_or_uuid
    )
    command = (
        f"mkviosbk -t {shlex.quote(backup_type)} "
        f"-m {shlex.quote(system_name)} --uuid {shlex.quote(vios_uuid)} "
        f"-f {shlex.quote(backup_name)}"
    )
    return await run_hmc_cli(command, hmc.config)


async def restore_vios(
    hmc: HMCClient,
    vios_name_or_uuid: str,
    backup_name: str,
    *,
    system_name_or_uuid: str,
    backup_type: RestoreBackupType,
    restart_if_required: bool = False,
) -> str:
    """Restore a VIOS backup and return the raw HMC CLI output."""
    validate_vios_restore_request(backup_name, backup_type)
    system_name, vios_uuid = await _resolve_vios_backup_selectors(
        hmc, system_name_or_uuid, vios_name_or_uuid
    )
    command = (
        f"rstviosbk -t {shlex.quote(backup_type)} "
        f"-m {shlex.quote(system_name)} --uuid {shlex.quote(vios_uuid)} "
        f"-f {shlex.quote(backup_name)}"
        f"{' -r' if restart_if_required else ''}"
    )
    return await run_hmc_cli(command, hmc.config)
