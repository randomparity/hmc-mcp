"""LPAR ownership authorization and identity resolution operations."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from ... import audit
from ...client import HMCClient
from ...client.client_resolution import (
    MAX_PARENT_DISCOVERY_SYSTEMS,
    PARENT_DISCOVERY_TIMEOUT_SECONDS,
)
from ...errors import HMCError
from ...resource_identity import is_uuid, resolve_lpar_uuid, resolve_system_uuid
from ...ssh.transport import HMCCLIError
from ...ssh.lpar import resolve_system_cli_name, stamp_lpar_ownership
from ...ssh.description_validation import validate_lpar_description
from ...ssh.profiles import get_lpar_description, set_lpar_description

_logger = logging.getLogger(__name__)

def _fleet_within_discovery_bound(
    systems: list[dict[str, Any]], lpar_label: str
) -> list[dict[str, Any]]:
    """Reject an owning-system search whose request fan-out is too large.

    The bound is `client_resolution`'s, but not its message: that one reads
    "ambiguous LPAR name", and nothing is ambiguous here — the partition UUID
    resolved uniquely before this ran.
    """
    if len(systems) > MAX_PARENT_DISCOVERY_SYSTEMS:
        raise ValueError(
            f"Cannot identify the managed system owning LPAR {lpar_label!r}: "
            f"discovery exceeds {MAX_PARENT_DISCOVERY_SYSTEMS} managed systems; "
            "supply managed-system scope"
        )
    return systems


def _discovery_candidate(system: dict[str, Any]) -> tuple[str, str] | None:
    """Return one fleet entry's UUID and CLI name, or None if unusable."""
    system_uuid = system.get("UUID")
    system_name = (system.get("Resource") or {}).get("SystemName")
    if not isinstance(system_uuid, str) or not system_uuid:
        return None
    if not isinstance(system_name, str) or not system_name:
        return None
    return system_uuid, system_name


def _hosts_partition(partitions: list[dict[str, Any]], lpar_uuid: str) -> bool:
    """Whether *partitions* contains *lpar_uuid*, compared case-insensitively.

    ``resolve_lpar_uuid`` returns a caller-supplied UUID verbatim and ``is_uuid``
    admits upper-case hex, while the HMC renders UUIDs lower-case. These are the
    only places in the package where a *user-supplied* UUID is equality-compared
    against HMC output, so the normalisation belongs here rather than in the
    shared resolver, whose value also reaches URL path segments.
    """
    wanted = lpar_uuid.casefold()
    return any(
        str(entry.get("UUID") or "").casefold() == wanted for entry in partitions
    )


@dataclass
class _SkippedFrames:
    """Frames the owning-system walk could not read, bounded for reporting."""

    unreadable: list[str] = field(default_factory=list)
    unusable_entries: int = 0

    @property
    def total(self) -> int:
        return len(self.unreadable) + self.unusable_entries

    def describe(self) -> str:
        """Render the skips as bounded evidence, never one line per frame."""
        if not self.total:
            return ""
        shown = self.unreadable[:_MAX_REPORTED_SKIPS]
        parts = list(shown)
        if len(self.unreadable) > len(shown):
            parts.append(f"and {len(self.unreadable) - len(shown)} more")
        if self.unusable_entries:
            parts.append(f"{self.unusable_entries} with incomplete inventory metadata")
        return f" ({self.total} could not be read: {', '.join(parts)})"


_MAX_REPORTED_SKIPS = 5


async def _fleet_inventory(hmc: HMCClient, lpar_label: str) -> list[dict[str, Any]]:
    """Read the fleet, translating an inventory failure into the same remedy.

    The unfiltered ``ManagedSystem`` feed is the one some HMC firmware builds
    answer with HTTP 500 (``client_systems.list_managed_systems``), and a
    selector-less DLPAR call reads it where it previously read nothing. Every
    other discovery failure names ``supply managed-system scope``; without this
    the one degraded-dependency case that the selector *does* fix would be the
    only one that never mentions it.
    """
    try:
        return await hmc.list_managed_systems()
    except HMCError as exc:
        raise ValueError(
            f"Cannot identify the managed system owning LPAR {lpar_label!r}: "
            f"managed-system inventory is unavailable ({exc}); "
            "supply managed-system scope"
        ) from exc


async def _search_fleet_for_partition(
    hmc: HMCClient, lpar_uuid: str, lpar_label: str, skipped: _SkippedFrames
) -> tuple[str, str] | None:
    """Return the system containing *lpar_uuid*, recording frames it could not read."""
    for system in _fleet_within_discovery_bound(
        await _fleet_inventory(hmc, lpar_label), lpar_label
    ):
        candidate = _discovery_candidate(system)
        if candidate is None:
            skipped.unusable_entries += 1
            continue
        system_uuid, system_name = candidate
        try:
            partitions = await hmc.list_logical_partitions(system_uuid)
        except HMCError as exc:
            _logger.warning(
                "Owning-system discovery could not read managed system %s: %s",
                system_uuid,
                exc,
                exc_info=exc,
            )
            skipped.unreadable.append(system_uuid)
            continue
        if _hosts_partition(partitions, lpar_uuid):
            _logger.info(
                "LPAR %s authorized against discovered managed system %s (%s)",
                lpar_uuid,
                system_name,
                system_uuid,
            )
            return system_uuid, system_name
    return None


async def _discover_owning_system(
    hmc: HMCClient, lpar_uuid: str, lpar_label: str
) -> tuple[str, str]:
    """Return the UUID and CLI name of the system that contains *lpar_uuid*.

    ADR 0094. The ADR 0011 guard reads an ownership token by CLI system name
    plus partition name, so an operation whose managed-system selector is
    optional (ADR 0063) has to derive one when the caller omits it. This is the
    bounded parent discovery :meth:`HMCClient.find_partition_by_name` already
    applies to a fleet-ambiguous partition name — the same 100-system fan-out
    cap, the same timeout, and the same "supply managed-system scope" remedy.

    The name comes from the inventory entry that matched, so *on this branch*
    the guard cannot fall back to running ``lssyscfg -m <uuid>``, which the HMC
    CLI cannot satisfy. The selector-supplied branch still hands ``_system_name``
    the caller's raw selector, which that helper's pre-existing degraded path
    can return verbatim.

    A frame whose partition feed errors, or whose inventory entry is unusable,
    is skipped rather than made fatal: the walk crosses frames the caller never
    named, and one unhealthy frame sorting early must not take DLPAR down for
    every partition in the fleet. Skipping never widens what may be mutated —
    the search still ends in a raise unless it *positively* matched the
    partition — and the raise names how many frames went unread, so a degraded
    fleet is diagnosed rather than reported as an absent partition.
    """
    skipped = _SkippedFrames()
    try:
        async with asyncio.timeout(PARENT_DISCOVERY_TIMEOUT_SECONDS):
            found = await _search_fleet_for_partition(
                hmc, lpar_uuid, lpar_label, skipped
            )
    except TimeoutError as exc:
        raise ValueError(
            f"Cannot identify the managed system owning LPAR {lpar_label!r}: "
            "parent discovery timed out; supply managed-system scope"
        ) from exc
    if found is not None:
        return found
    raise ValueError(
        f"Cannot identify the managed system owning LPAR {lpar_label!r}: "
        f"no managed system reports it{skipped.describe()}; "
        "supply managed-system scope"
    )


async def _verify_partition_on_system(
    hmc: HMCClient, system_uuid: str, lpar_uuid: str, lpar_selector: str
) -> None:
    """Reject a partition UUID that does not live on the selected system.

    ADR 0094. ``resolve_lpar_uuid`` passes a canonical UUID straight through, so
    a caller can pair any partition UUID with any managed-system selector —
    and ADR 0039 actively recommends UUIDs in policy allowlists, so that pairing
    is the recommended input shape. Without this check the guard would read
    ``lssyscfg -m <the selected system> --filter lpar_names=<the partition's
    name>``: on a cross-system name collision that reads a *different*
    partition's token, and can approve mutating a foreign-owned one. The
    omitted-selector path gets the same containment from
    :func:`_discover_owning_system`'s UUID match.

    Only a UUID needs the read, so *lpar_selector* must be the string the caller
    passed, not a resolved value: a partition *name* was resolved through
    ``find_partition_by_name`` against this very feed, which establishes its
    containment already, and re-reading the largest payload in the guarded chain
    would answer a settled question.
    """
    if not is_uuid(lpar_selector):
        return
    try:
        partitions = await hmc.list_logical_partitions(system_uuid)
    except HMCError as exc:
        raise ValueError(
            f"Cannot confirm LPAR {lpar_selector!r} belongs to managed system "
            f"{system_uuid} ({exc}); retry, or omit the selector to have the "
            "system discovered"
        ) from exc
    if not _hosts_partition(partitions, lpar_uuid):
        raise ValueError(
            f"LPAR {lpar_selector!r} does not belong to managed system "
            f"{system_uuid}; name the managed system that hosts it, or omit "
            "the selector to have it discovered"
        )


async def resolve_and_authorize_lpar(
    hmc: HMCClient,
    lpar_name_or_uuid: str,
    system_name_or_uuid: str | None,
    *,
    ownership_override: bool,
) -> str:
    """Resolve one LPAR, authorize the mutation, and return its UUID.

    The guarded counterpart of the resolve chain in :func:`rename_lpar`, for the
    operations whose managed-system selector is optional. Either way the
    partition is established to live on the system whose token the guard reads:
    by discovery when the selector is omitted, by
    :func:`_verify_partition_on_system` when it is supplied (ADR 0092).

    An approved override reads no token, so it skips the fleet walk the guard
    alone requires — otherwise an oversized fleet, a slow one, or an unreachable
    owning frame would *block* the operator's exception on exactly the degraded
    inventory that provokes it (ADR 0092 §4). It still resolves the partition
    name, one REST read, so the audit record for a deliberate bypass of the
    ownership control names the partition in the same vocabulary as every other
    override record.

    A blank *system_name_or_uuid* is read as absent on both branches: MCP
    clients that serialise an unset optional string as ``""`` sent it before
    this operation existed, and it was ignored.
    """
    selector = (system_name_or_uuid or "").strip() or None
    if ownership_override:
        return await _authorize_override(hmc, lpar_name_or_uuid, selector)
    if selector is None:
        lpar_uuid = await resolve_lpar_uuid(hmc, lpar_name_or_uuid)
        # Establish the partition exists *before* the walk: `is_uuid` is a format
        # check, so an unknown or non-partition UUID would otherwise drive up to
        # 100 full partition-feed reads from one caller-supplied string.
        lpar_name = await _partition_name(hmc, lpar_uuid, lpar_name_or_uuid)
        system_uuid, system_name = await _discover_owning_system(
            hmc, lpar_uuid, lpar_name_or_uuid
        )
    else:
        system_uuid = await resolve_system_uuid(hmc, selector)
        lpar_uuid = await resolve_lpar_uuid(
            hmc, lpar_name_or_uuid, system_name_or_uuid=system_uuid
        )
        await _verify_partition_on_system(
            hmc, system_uuid, lpar_uuid, lpar_name_or_uuid
        )
        system_name, lpar_name = await resolve_lpar_ownership_names(
            hmc, system_uuid, selector, lpar_uuid
        )
    await authorize_lpar_mutation(hmc, system_name, lpar_name)
    return lpar_uuid


async def _partition_name(hmc: HMCClient, lpar_uuid: str, lpar_label: str) -> str:
    """Read one partition's CLI name, and fail if the UUID has no partition.

    Two different failures, only one of which this function raises. A UUID that
    names nothing is rejected by the GET itself — ``client._get`` raises
    ``HMCError`` on the 404 and it propagates from here unchanged, naming the
    partition the caller got wrong. The ``ValueError`` below covers the narrower
    case of a resource that reads back but carries no ``PartitionName``.

    Both stop the caller before the ADR 0094 fleet walk, which is the point:
    ``is_uuid`` is a format check, so without this the walk would read up to
    ``MAX_PARENT_DISCOVERY_SYSTEMS`` partition feeds before reporting a missing
    partition as a missing *system*.
    """
    lpar = await hmc.get_logical_partition(lpar_uuid)
    lpar_name = ((lpar or {}).get("Resource") or {}).get("PartitionName")
    if not lpar_name:
        raise ValueError(
            f"No LPAR {lpar_label!r} found. "
            "Use hmc_list_lpars to list available partitions."
        )
    return str(lpar_name)


async def _authorize_override(
    hmc: HMCClient, lpar_name_or_uuid: str, selector: str | None
) -> str:
    """Audit an operator-approved ownership bypass and return the LPAR's UUID."""
    lpar_uuid = await resolve_lpar_uuid(
        hmc, lpar_name_or_uuid, system_name_or_uuid=selector
    )
    lpar_name = await _partition_name(hmc, lpar_uuid, lpar_name_or_uuid)
    await authorize_lpar_mutation(
        hmc, selector or "", lpar_name, ownership_override=True
    )
    return lpar_uuid


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
    system_name = await _resolve_system_name(hmc, system_uuid, system_name_or_uuid)
    lpar = await hmc.get_logical_partition(lpar_uuid)
    lpar_name = ((lpar or {}).get("Resource") or {}).get("PartitionName")
    if not lpar_name:
        raise ValueError(f"LPAR {lpar_uuid!r} has no partition name")
    return system_name, lpar_name


async def resolve_and_authorize_lpar_mutation(
    hmc: HMCClient,
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    *,
    ownership_override: bool = False,
) -> tuple[str, str]:
    """Resolve an LPAR's CLI names, authorize its mutation, and return the names."""
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    lpar_uuid = await resolve_lpar_uuid(
        hmc, lpar_name_or_uuid, system_name_or_uuid=system_uuid
    )
    names = await resolve_lpar_ownership_names(
        hmc, system_uuid, system_name_or_uuid, lpar_uuid
    )
    await authorize_lpar_mutation(
        hmc, *names, ownership_override=ownership_override
    )
    return names


async def _resolve_system_name(hmc: HMCClient, system_uuid: str, fallback: str) -> str:
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
        return await resolve_system_cli_name(hmc.config, system_uuid)
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

    system_name = await _resolve_system_name(hmc, system_uuid, system_fallback)
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
    system_name, lpar_name = await resolve_and_authorize_lpar_mutation(
        hmc,
        system_name_or_uuid,
        lpar_name_or_uuid,
        ownership_override=ownership_override,
    )
    return await set_lpar_description(hmc.config, system_name, lpar_name, description)
