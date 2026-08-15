"""Shared LPAR creation and ownership operations."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from .client import HMCClient
from .common import resolve_lpar_uuid, resolve_system_uuid
from .documents import (
    Keylock,
    LparResources,
    OsType,
    PartitionType,
    build_lpar_document,
)
from .errors import HMCError
from .jobs import (
    power_off_lpar_job,
    power_on_lpar_job,
    validate_wait_timing,
    wait_for_submitted_job,
)
from .ssh import HMCCLIError
from .ssh_commands import _ssh_system_name, create_lpar_via_cli, stamp_lpar_ownership
from .ssh_commands import get_lpar_description

_logger = logging.getLogger(__name__)
_OWNERSHIP_TOKEN = re.compile(
    r"\[hmc-mcp owner:(?P<owner>[^\s\[\]:]+) created:\d{4}-\d{2}-\d{2}\]"
)


@dataclass(frozen=True)
class LparCreation:
    """Inputs needed by both REST and CLI LPAR creation paths."""

    name: str
    partition_type: PartitionType
    resources: LparResources
    partition_id: int | None = None
    os_type: OsType | None = None
    keylock: Keylock | None = None
    max_virtual_slots: int | None = None


@dataclass(frozen=True)
class LparCreationResult:
    """Result shared by direct creation and provisioning workflows."""

    resource_created: bool
    lpar: dict[str, Any] | None
    ownership_stamped: bool | None
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class LparPowerResult:
    """An LPAR power outcome paired with its resolved identity."""

    lpar_uuid: str
    job: dict[str, Any] | None


@dataclass(frozen=True)
class LparPowerOnOutcome:
    """Stable public result for an LPAR PowerOn request."""

    already_running: bool
    job: dict[str, Any] | None
    message: str | None


def power_on_outcome(result: LparPowerResult) -> LparPowerOnOutcome:
    """Normalize submitted and already-running PowerOn results."""
    job = result.job
    if job is not None and job.get("already_running") is True:
        message = job.get("message")
        return LparPowerOnOutcome(
            already_running=True,
            job=None,
            message=message if isinstance(message, str) else None,
        )
    return LparPowerOnOutcome(already_running=False, job=job, message=None)


def parse_lpar_ownership_owner(description: str) -> str | None:
    """Return the advisory hmc-mcp owner token embedded in *description*."""
    match = _OWNERSHIP_TOKEN.search(description)
    return match.group("owner") if match is not None else None


def _audit_lpar_ownership_override(
    hmc: HMCClient, system_name: str, lpar_name: str
) -> None:
    _logger.warning(
        "LPAR ownership override approved",
        extra={
            "hmc_system": system_name,
            "hmc_lpar": lpar_name,
            "hmc_agent_id": hmc.config.agent_id or "hmc-mcp",
        },
    )


def _authorize_lpar_ownership_description(
    hmc: HMCClient,
    system_name: str,
    lpar_name: str,
    description: str,
    *,
    ownership_override: bool = False,
) -> str | None:
    """Authorize a supplied description snapshot and return its parsed owner."""
    owner = parse_lpar_ownership_owner(description)
    if ownership_override:
        _audit_lpar_ownership_override(hmc, system_name, lpar_name)
        return owner
    if owner is None:
        if "[hmc-mcp" in description:
            raise PermissionError(
                f"LPAR {lpar_name!r} has a malformed hmc-mcp ownership token; "
                "retry only with ownership_override=true after operator approval"
            )
        return None
    current_owner = hmc.config.agent_id or "hmc-mcp"
    if owner != current_owner:
        raise PermissionError(
            f"LPAR {lpar_name!r} is owned by {owner!r}, not {current_owner!r}; "
            "retry only with ownership_override=true after operator approval"
        )
    return owner


async def authorize_decommission_lpar_ownership_snapshot(
    hmc: HMCClient,
    system_name: str,
    lpar_name: str,
    *,
    ownership_override: bool,
) -> str | None:
    """Read and authorize one ownership snapshot for LPAR decommission."""
    description = await get_lpar_description(hmc.config, system_name, lpar_name)
    return _authorize_lpar_ownership_description(
        hmc,
        system_name,
        lpar_name,
        description,
        ownership_override=ownership_override,
    )


async def authorize_lpar_mutation(
    hmc: HMCClient,
    system_name: str,
    lpar_name: str,
    *,
    ownership_override: bool = False,
) -> None:
    """Reject mutations of foreign-owned or malformed ownership-stamped LPARs."""
    if ownership_override:
        _audit_lpar_ownership_override(hmc, system_name, lpar_name)
        return
    description = await get_lpar_description(hmc.config, system_name, lpar_name)
    _authorize_lpar_ownership_description(hmc, system_name, lpar_name, description)


async def resolve_lpar_ownership_names(
    hmc: HMCClient,
    system_uuid: str,
    system_name_or_uuid: str,
    lpar_uuid: str,
) -> tuple[str, str]:
    """Resolve the CLI names required to read an LPAR ownership token."""
    system_name = await _system_name(hmc, system_uuid, system_name_or_uuid)
    lpar = await hmc.get_logical_partition(lpar_uuid)
    lpar_name = ((lpar or {}).get("Resource") or {}).get("PartitionName")
    if not lpar_name:
        raise ValueError(f"LPAR {lpar_uuid!r} has no partition name")
    return system_name, lpar_name


async def _system_name(hmc, system_uuid: str, fallback: str) -> str:
    try:
        system = await hmc.get_managed_system(system_uuid)
        name = ((system or {}).get("Resource") or {}).get("SystemName")
        if name:
            return name
    except HMCError as exc:
        _logger.debug(
            "REST system-name lookup failed for %s: %s",
            system_uuid,
            exc,
            exc_info=exc,
        )
    try:
        return await _ssh_system_name(hmc.config, system_uuid)
    except HMCCLIError as exc:
        _logger.warning(
            "SSH system-name lookup failed for %s; using fallback %r: %s",
            system_uuid,
            fallback,
            exc,
            exc_info=exc,
        )
        return fallback


async def stamp_created_lpar_ownership(
    hmc: HMCClient,
    system_uuid: str,
    system_fallback: str,
    created_lpar: dict[str, Any],
) -> tuple[bool | None, list[str]]:
    confirmed_name = (created_lpar.get("Resource") or {}).get("PartitionName")
    if not confirmed_name:
        return None, ["ownership stamp skipped: create result has no partition name"]

    system_name = await _system_name(hmc, system_uuid, system_fallback)
    if system_name == system_uuid:
        return None, [
            f"ownership stamp skipped for LPAR {confirmed_name!r}: "
            "could not resolve the managed-system name"
        ]

    token = await stamp_lpar_ownership(
        hmc.config, system_name, confirmed_name, agent_id=hmc.config.agent_id
    )
    if token is not None:
        return True, []
    _logger.warning(
        "ownership stamp failed for LPAR %r on %r", confirmed_name, system_name
    )
    return False, [f"ownership stamp failed for LPAR {confirmed_name!r}"]


async def create_and_stamp_lpar(
    hmc: HMCClient,
    system_name_or_uuid: str,
    creation: LparCreation,
) -> LparCreationResult:
    """Validate and create an LPAR with fallback and ownership stamping."""
    existing = await hmc.find_partition_by_name(creation.name)
    if existing:
        raise ValueError(
            f"An LPAR named {creation.name!r} already exists "
            f"(UUID {existing.get('UUID')!r}). Choose a different name "
            "or delete the existing partition first."
        )
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    system_name: str | None = None
    document = build_lpar_document(
        name=creation.name,
        partition_type=creation.partition_type,
        partition_id=creation.partition_id,
        resources=creation.resources,
        os_type=creation.os_type,
        keylock=creation.keylock,
        max_virtual_slots=creation.max_virtual_slots,
    )
    try:
        created_lpar = await hmc.create_logical_partition(system_uuid, document)
    except HMCError as exc:
        if exc.status_code != 406:
            raise
        try:
            system_name = await _ssh_system_name(hmc.config, system_uuid)
        except HMCCLIError:
            system_name = system_name_or_uuid
        resources = creation.resources
        await create_lpar_via_cli(
            hmc.config,
            system_name=system_name,
            name=creation.name,
            partition_type=creation.partition_type,
            resources=resources,
            max_virtual_slots=creation.max_virtual_slots,
        )
        created_lpar = await hmc.find_partition_by_name(creation.name)

    if created_lpar is None:
        return LparCreationResult(
            resource_created=True,
            lpar=None,
            ownership_stamped=None,
            warnings=(
                f"ownership stamp skipped for LPAR {creation.name!r}: "
                "create returned no LPAR body",
            ),
        )
    ownership_stamped, warnings = await stamp_created_lpar_ownership(
        hmc, system_uuid, system_name or system_name_or_uuid, created_lpar
    )
    return LparCreationResult(True, created_lpar, ownership_stamped, tuple(warnings))


async def delete_lpar(
    hmc: HMCClient,
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    *,
    ownership_override: bool = False,
) -> str:
    """Authorize and delete a powered-off LPAR, returning its UUID."""
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    lpar_uuid = await resolve_lpar_uuid(
        hmc, lpar_name_or_uuid, system_name_or_uuid=system_uuid
    )
    system_name, lpar_name = await resolve_lpar_ownership_names(
        hmc, system_uuid, system_name_or_uuid, lpar_uuid
    )
    await authorize_lpar_mutation(
        hmc,
        system_name,
        lpar_name,
        ownership_override=ownership_override,
    )
    state = await hmc.get_quick_property(
        "LogicalPartition", lpar_uuid, "PartitionState"
    )
    if state != "not activated":
        raise HMCError(
            f"Cannot delete LPAR {lpar_uuid} — current state is {state!r}; "
            "it must be 'not activated' to delete. Power off the partition "
            "and verify its state before retrying.",
            status_code=409,
        )
    await hmc.delete_logical_partition(lpar_uuid)
    return lpar_uuid


async def power_lpar(
    hmc: HMCClient,
    lpar_name_or_uuid: str,
    *,
    power_on: bool,
    system_name_or_uuid: str | None = None,
    immediate: bool = False,
    force: bool = False,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
) -> LparPowerResult:
    """Apply shared LPAR power policy, submit the job, and optionally wait."""
    validate_wait_timing(wait, timeout_seconds, poll_interval)
    lpar_uuid = await resolve_lpar_uuid(
        hmc, lpar_name_or_uuid, system_name_or_uuid=system_name_or_uuid
    )
    if power_on and not force:
        state = await hmc.get_quick_property(
            "LogicalPartition", lpar_uuid, "PartitionState"
        )
        if state == "running":
            return LparPowerResult(
                lpar_uuid,
                {
                    "already_running": True,
                    "message": (
                        f"LPAR {lpar_uuid} is already running. "
                        "Use force=True to submit PowerOn anyway."
                    ),
                },
            )
    operation = "PowerOn" if power_on else "PowerOff"
    document = (
        power_on_lpar_job() if power_on else power_off_lpar_job(immediate=immediate)
    )
    job = await hmc.submit_job(
        f"/rest/api/uom/LogicalPartition/{lpar_uuid}/do/{operation}", document
    )
    selected_job = await wait_for_submitted_job(
        hmc, job, wait, timeout_seconds, poll_interval
    )
    return LparPowerResult(lpar_uuid, selected_job)


async def rename_lpar(
    hmc: HMCClient,
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    new_name: str,
    *,
    ownership_override: bool = False,
) -> tuple[str, dict[str, Any] | None]:
    """Resolve, authorize, and rename one LPAR."""
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    lpar_uuid = await resolve_lpar_uuid(
        hmc, lpar_name_or_uuid, system_name_or_uuid=system_uuid
    )
    system_name, lpar_name = await resolve_lpar_ownership_names(
        hmc, system_uuid, system_name_or_uuid, lpar_uuid
    )
    await authorize_lpar_mutation(
        hmc,
        system_name,
        lpar_name,
        ownership_override=ownership_override,
    )
    updated = await hmc.modify_logical_partition(
        lpar_uuid, build_lpar_document(name=new_name)
    )
    return lpar_uuid, updated
