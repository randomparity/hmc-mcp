"""LPAR ownership authorization and identity resolution."""

from __future__ import annotations

import logging
import re
from typing import Any

from . import audit
from .client import HMCClient
from .errors import HMCError
from .resource_identity import resolve_lpar_uuid, resolve_system_uuid
from .ssh.transport import HMCCLIError
from .ssh.lpar import _ssh_system_name, stamp_lpar_ownership
from .ssh.description_validation import validate_lpar_description
from .ssh.profiles import get_lpar_description, set_lpar_description

_logger = logging.getLogger(__name__)

_OWNERSHIP_TOKEN = re.compile(
    r"\[hmc-mcp owner:(?P<owner>[^\s\[\]:]+) created:\d{4}-\d{2}-\d{2}\]"
)
_CALLER_TOKEN = re.compile(
    r"\[hmc-mcp owner:[^\s\[\]:]+ created:\d{4}-\d{2}-\d{2}\] "
    r"\[caller (?P<token>[^\s\[\]]+)\]"
)


def parse_lpar_ownership_owner(description: str) -> str | None:
    """Return the advisory hmc-mcp owner token embedded in *description*."""
    match = _OWNERSHIP_TOKEN.search(description)
    return match.group("owner") if match is not None else None


def parse_lpar_ownership_caller_token(description: str) -> str | None:
    """Return the unique caller token following a well-formed ownership stamp."""
    matches = _CALLER_TOKEN.findall(description)
    if len(matches) != 1 or description.count("[caller ") != 1:
        return None
    return matches[0]


def lpar_ownership_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Distill one parsed LogicalPartition feed entry into ownership facts."""
    resource = entry.get("Resource") or {}
    description = resource.get("Description")
    owner = (
        parse_lpar_ownership_owner(description)
        if isinstance(description, str)
        else None
    )
    return {
        "lpar_name": resource.get("PartitionName"),
        "lpar_uuid": entry.get("UUID"),
        "description": description,
        "owned": owner is not None,
        "owner": owner,
        "unparsed": description is not None and owner is None,
    }


async def list_lpar_ownership(
    hmc: HMCClient,
    system_name_or_uuid: str | None = None,
) -> list[dict[str, Any]]:
    """Read parsed ownership for every LPAR on one system or across the fleet."""
    if system_name_or_uuid is not None:
        system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
        entries = await hmc.list_logical_partitions(system_uuid)
    else:
        entries = await hmc.list_uom("LogicalPartition")
    return [lpar_ownership_entry(entry) for entry in entries]


def _audit_lpar_ownership_override(
    hmc: HMCClient, system_name: str, lpar_name: str
) -> None:
    audit.record_ownership_override(
        system=system_name,
        lpar=lpar_name,
        host=hmc.config.host,
        agent_id=hmc.config.agent_id or "hmc-mcp",
    )


def _audit_lpar_ownership_denied(
    hmc: HMCClient,
    system_name: str,
    lpar_name: str,
    *,
    operation: audit.OwnershipOperation,
    denial: audit.OwnershipDenial,
    owner: str | None,
) -> None:
    audit.record_ownership_denied(
        operation=operation,
        denial=denial,
        system=system_name,
        lpar=lpar_name,
        owner=owner,
        host=hmc.config.host,
        agent_id=hmc.config.agent_id or "hmc-mcp",
    )


def authorize_lpar_ownership_description(
    hmc: HMCClient,
    system_name: str,
    lpar_name: str,
    description: str,
    *,
    operation: audit.OwnershipOperation,
    ownership_override: bool = False,
) -> str | None:
    """Authorize a supplied description snapshot and return its parsed owner."""
    owner = parse_lpar_ownership_owner(description)
    if ownership_override:
        _audit_lpar_ownership_override(hmc, system_name, lpar_name)
        return owner
    if owner is None:
        if "[hmc-mcp" in description:
            _audit_lpar_ownership_denied(
                hmc,
                system_name,
                lpar_name,
                operation=operation,
                denial="malformed-token",
                owner=None,
            )
            raise PermissionError(
                f"LPAR {lpar_name!r} has a malformed hmc-mcp ownership token; "
                "retry only with ownership_override=true after operator approval"
            )
        return None
    current_owner = hmc.config.agent_id or "hmc-mcp"
    if owner != current_owner:
        _audit_lpar_ownership_denied(
            hmc,
            system_name,
            lpar_name,
            operation=operation,
            denial="foreign-owner",
            owner=owner,
        )
        raise PermissionError(
            f"LPAR {lpar_name!r} is owned by {owner!r}, not {current_owner!r}; "
            "retry only with ownership_override=true after operator approval"
        )
    return owner


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
    authorize_lpar_ownership_description(
        hmc, system_name, lpar_name, description, operation="lpar-mutation"
    )


async def resolve_lpar_ownership_names(
    hmc: HMCClient,
    system_uuid: str,
    system_name_or_uuid: str,
    lpar_uuid: str,
) -> tuple[str, str]:
    """Resolve the CLI names required to read an LPAR ownership token."""
    system_name = await resolve_system_name(hmc, system_uuid, system_name_or_uuid)
    lpar = await hmc.get_logical_partition(lpar_uuid)
    lpar_name = ((lpar or {}).get("Resource") or {}).get("PartitionName")
    if not lpar_name:
        raise ValueError(f"LPAR {lpar_uuid!r} has no partition name")
    return system_name, lpar_name


async def resolve_system_name(hmc: HMCClient, system_uuid: str, fallback: str) -> str:
    """Resolve an HMC CLI system name, falling back to the caller's selector."""
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


async def authorize_decommission_lpar_ownership_snapshot(
    hmc: HMCClient,
    system_name: str,
    lpar_name: str,
    *,
    ownership_override: bool,
) -> str | None:
    """Read and authorize one ownership snapshot for LPAR decommission."""
    description = await get_lpar_description(hmc.config, system_name, lpar_name)
    return authorize_lpar_ownership_description(
        hmc,
        system_name,
        lpar_name,
        description,
        operation="lpar-decommission-snapshot",
        ownership_override=ownership_override,
    )


async def stamp_created_lpar_ownership(
    hmc: HMCClient,
    system_uuid: str,
    system_fallback: str,
    created_lpar: dict[str, Any],
    caller_token: str | None = None,
) -> tuple[bool | None, list[str]]:
    """Stamp a newly created LPAR and report confirmed, skipped, or failed."""
    confirmed_name = (created_lpar.get("Resource") or {}).get("PartitionName")
    if not confirmed_name:
        return None, ["ownership stamp skipped: create result has no partition name"]

    system_name = await resolve_system_name(hmc, system_uuid, system_fallback)
    if system_name == system_uuid:
        return None, [
            f"ownership stamp skipped for LPAR {confirmed_name!r}: "
            "could not resolve the managed-system name"
        ]

    token = await stamp_lpar_ownership(
        hmc.config,
        system_name,
        confirmed_name,
        agent_id=hmc.config.agent_id,
        caller_token=caller_token,
    )
    if token is not None:
        return True, []
    _logger.warning(
        "ownership stamp failed for LPAR %r on %r", confirmed_name, system_name
    )
    return False, [f"ownership stamp failed for LPAR {confirmed_name!r}"]


async def set_lpar_ownership_description(
    hmc: HMCClient,
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    description: str,
    *,
    ownership_override: bool = False,
) -> str:
    """Validate, authorize, and write one LPAR ownership description."""
    validate_lpar_description(description)
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
    return await set_lpar_description(hmc.config, system_name, lpar_name, description)
