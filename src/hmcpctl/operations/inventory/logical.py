"""The logical inventory behind ``hmc_inventory`` (ADR 0196).

Composes the managed-system, capacity, partition and ownership reads of one
connection into one bounded page. Each source is authorized through *admit* as
the tool it delegates to, before it is read, so this module never sees the
access policy. The contract is ``docs/workflow/specs/2026-10-02-logical-inventory-design.md``.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from ...errors import HMCError, HMCTransportError
from ...resource_identity import is_uuid
from ...xmlutil import leaf_text
from ..lpar.ownership import lpar_ownership_entry
from ..partition_state import PartitionState
from .capacity import system_capacity
from .composite import _container, _processor_figures

SYSTEMS_TOOL = "hmc_list_systems"
PARTITIONS_TOOL = "hmc_list_lpars"
CAPACITY_TOOL = "hmc_capacity_report"
OWNERSHIP_TOOL = "hmc_list_lpar_ownership"
DELEGATED_TOOLS = (SYSTEMS_TOOL, PARTITIONS_TOOL, CAPACITY_TOOL, OWNERSHIP_TOOL)

MAX_SELECTORS = 16
SYSTEMS_PER_PAGE = 16
MAX_LIMIT = 200
MAX_OWNER = 64
_MAX_CURSOR = 256
_MAX_DETAIL = 500
_INVALID_CURSOR = "invalid_cursor: pass next_cursor from a previous page unchanged"
_SELECTOR_HINT = " Pass systems selectors to read named systems."

Admit = Callable[[str, str | None], str | None]
"""Asks whether a delegated tool may run for a system selector (or ``None``).

Returns ``None`` when admitted, otherwise the denial text.
"""

SourceState = Literal["ok", "unavailable", "denied"]


@dataclass(frozen=True)
class SourceStatus:
    """Whether one source answered, and through which delegated tool."""

    status: SourceState
    tool: str
    detail: str | None = None


@dataclass(frozen=True)
class SystemSources:
    """The per-system sources. Ownership has no read of its own."""

    capacity: SourceStatus
    partitions: SourceStatus
    ownership: SourceStatus


@dataclass(frozen=True)
class InventorySystem:
    """One managed system, or a selector that did not resolve to one.

    ``id`` is ``<connection>/<system uuid>``. Capacity figures are ``None`` when
    unknown or withheld, never zero; ``sources`` says which.
    """

    id: str | None
    selector: str | None
    uuid: str | None
    name: str | None
    state: str | None
    total_memory_mib: int | None
    free_memory_mib: int | None
    total_proc_units: float | None
    free_proc_units: float | None
    sources: SystemSources


@dataclass(frozen=True)
class InventoryPartition:
    """One logical partition. ``id`` is ``<connection>/<system uuid>/<uuid>``."""

    id: str
    system_id: str
    uuid: str
    name: str | None
    partition_id: int | None
    partition_type: str | None
    state: str | None
    rmc_state: str | None
    current_memory_mib: int | None
    current_proc_units: float | None
    dedicated_procs: bool | None
    owned: bool | None
    owner: str | None


@dataclass(frozen=True)
class InventoryPage:
    """At most ``systems_limit`` systems and ``limit`` partitions of one connection.

    ``truncated`` means more remains to read; pass ``next_cursor`` as ``cursor``.
    ``systems_source`` is ``None`` when ``systems`` selectors named the systems.
    """

    connection: str
    systems_source: SourceStatus | None
    systems: list[InventorySystem]
    systems_limit: int
    systems_truncated: bool
    partitions: list[InventoryPartition]
    limit: int
    truncated: bool
    next_cursor: str | None


@dataclass(frozen=True)
class _Candidate:
    selector: str | None
    uuid: str
    entry: dict[str, Any]


def _status(tool: str, denial: str | None) -> SourceStatus:
    if denial is None:
        return SourceStatus("ok", tool)
    return SourceStatus("denied", tool, denial[:_MAX_DETAIL])


def _unavailable(tool: str, text: str) -> SourceStatus:
    return SourceStatus("unavailable", tool, text[:_MAX_DETAIL])


def encode_cursor(system_uuid: str, partition_uuid: str | None) -> str:
    raw = json.dumps([system_uuid, partition_uuid]).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def decode_cursor(cursor: str) -> tuple[str, str | None]:
    if len(cursor) > _MAX_CURSOR:
        raise ValueError(_INVALID_CURSOR)
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value = json.loads(raw.decode())
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise ValueError(_INVALID_CURSOR) from None
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(_INVALID_CURSOR)
    system, partition = value
    if not (isinstance(system, str) and is_uuid(system)):
        raise ValueError(_INVALID_CURSOR)
    if partition is not None and not (
        isinstance(partition, str) and is_uuid(partition)
    ):
        raise ValueError(_INVALID_CURSOR)
    return system, partition


def _check_inputs(systems: Sequence[str] | None, owner: str | None, limit: int) -> None:
    if systems is not None:
        if not 1 <= len(systems) <= MAX_SELECTORS:
            raise ValueError(
                f"systems: pass 1 to {MAX_SELECTORS} selectors, or omit it"
            )
        if not all(isinstance(value, str) and value for value in systems):
            raise ValueError("systems: every selector must be a non-empty name or UUID")
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit: must be 1 to {MAX_LIMIT}")
    if owner is not None and not 1 <= len(owner) <= MAX_OWNER:
        raise ValueError(f"owner: must be 1 to {MAX_OWNER} characters")


def _text(value: object) -> str | None:
    text = leaf_text(value)
    return text if isinstance(text, str) else None


def _int(value: object) -> int | None:
    text = _text(value)
    try:
        return int(text) if text is not None else None
    except ValueError:
        return None


def _float(value: object) -> float | None:
    text = _text(value)
    try:
        return float(text) if text is not None else None
    except ValueError:
        return None


async def _enumerate(hmc: Any, admit: Admit) -> tuple[SourceStatus, list[_Candidate]]:
    denial = admit(SYSTEMS_TOOL, None)
    if denial is not None:
        return _status(SYSTEMS_TOOL, denial + _SELECTOR_HINT), []
    try:
        entries = await hmc.list_managed_systems()
    except HMCError as exc:
        return _unavailable(SYSTEMS_TOOL, f"managed systems are unavailable: {exc}"), []
    candidates = [
        _Candidate(None, str(entry["UUID"]), entry)
        for entry in entries
        if entry.get("UUID")
    ]
    return _status(SYSTEMS_TOOL, None), candidates


def _unresolved(selector: str, status: SourceStatus) -> InventorySystem:
    return InventorySystem(
        id=None,
        selector=selector,
        uuid=None,
        name=None,
        state=None,
        total_memory_mib=None,
        free_memory_mib=None,
        total_proc_units=None,
        free_proc_units=None,
        sources=SystemSources(capacity=status, partitions=status, ownership=status),
    )


async def _resolve(
    hmc: Any, admit: Admit, selectors: Sequence[str]
) -> tuple[list[InventorySystem], list[_Candidate]]:
    """Admit, then resolve, each distinct selector, as ``resolve_system_uuid`` does."""
    unresolved: list[InventorySystem] = []
    candidates: list[_Candidate] = []
    stopped: str | None = None
    for selector in dict.fromkeys(selectors):
        denial = admit(PARTITIONS_TOOL, selector)
        if denial is not None:
            unresolved.append(_unresolved(selector, _status(PARTITIONS_TOOL, denial)))
            continue
        if stopped is not None:
            unresolved.append(
                _unresolved(selector, _unavailable(PARTITIONS_TOOL, stopped))
            )
            continue
        try:
            if is_uuid(selector):
                entry = await hmc.get_managed_system(selector)
            else:
                entry = await hmc.find_system_by_name(selector)
        except HMCTransportError as exc:
            stopped = f"managed system {selector!r} is unavailable: {exc}"
            unresolved.append(
                _unresolved(selector, _unavailable(PARTITIONS_TOOL, stopped))
            )
            continue
        except (HMCError, ValueError) as exc:
            text = f"managed system {selector!r} is unavailable: {exc}"
            unresolved.append(
                _unresolved(selector, _unavailable(PARTITIONS_TOOL, text))
            )
            continue
        if not entry or not entry.get("UUID"):
            text = f"no managed system matches {selector!r}"
            unresolved.append(
                _unresolved(selector, _unavailable(PARTITIONS_TOOL, text))
            )
            continue
        candidates.append(_Candidate(selector, str(entry["UUID"]), entry))
    return unresolved, candidates


def _partition(
    entry: dict[str, Any], system_id: str, ownership_ok: bool
) -> InventoryPartition:
    resource = entry.get("Resource") or {}
    memory = _container(resource, "PartitionMemoryConfiguration")
    processors = _processor_figures(resource)
    ownership = lpar_ownership_entry(entry) if ownership_ok else {}
    uuid = str(entry["UUID"])
    return InventoryPartition(
        id=f"{system_id}/{uuid}",
        system_id=system_id,
        uuid=uuid,
        name=_text(resource.get("PartitionName")),
        partition_id=_int(resource.get("PartitionID")),
        partition_type=_text(resource.get("PartitionType")),
        state=_text(resource.get("PartitionState")),
        rmc_state=_text(resource.get("ResourceMonitoringControlState")),
        current_memory_mib=_int(memory.get("CurrentMemory")),
        current_proc_units=_float(processors["current_proc_units"]),
        dedicated_procs=processors["dedicated_procs"],
        owned=ownership.get("owned"),
        owner=ownership.get("owner"),
    )


@dataclass
class _Reader:
    """One page's per-system reads, sharing the lazily asked capacity decision."""

    hmc: Any
    admit: Admit
    connection: str
    enumerated: bool
    lpar_state: PartitionState | None
    owner: str | None
    capacity_denial: str | None = None
    capacity_asked: bool = False

    def capacity(self, entry: dict[str, Any]) -> tuple[SourceStatus, Any]:
        if not self.capacity_asked:
            self.capacity_denial = self.admit(CAPACITY_TOOL, None)
            self.capacity_asked = True
        if self.capacity_denial is not None:
            return _status(CAPACITY_TOOL, self.capacity_denial), None
        try:
            return _status(CAPACITY_TOOL, None), system_capacity(entry)
        except ValueError as exc:
            return _unavailable(CAPACITY_TOOL, str(exc)), None

    async def system(
        self, candidate: _Candidate, after: str | None
    ) -> tuple[InventorySystem, list[InventoryPartition], str | None]:
        """Read one system; the third value is a transport failure's detail."""
        system_id = f"{self.connection}/{candidate.uuid}"
        selector = candidate.selector or candidate.uuid
        partitions_status = _status(
            PARTITIONS_TOOL,
            self.admit(PARTITIONS_TOOL, candidate.uuid) if self.enumerated else None,
        )
        ownership_status = _status(OWNERSHIP_TOOL, self.admit(OWNERSHIP_TOOL, selector))
        entries: list[dict[str, Any]] = []
        stop: str | None = None
        if partitions_status.status == "ok":
            try:
                entries = await self.hmc.list_logical_partitions(candidate.uuid)
            except HMCError as exc:
                text = f"partitions of {candidate.uuid} are unavailable: {exc}"
                partitions_status = _unavailable(PARTITIONS_TOOL, text)
                stop = text if isinstance(exc, HMCTransportError) else None
        if partitions_status.status != "ok":
            detail = f"ownership is read from {PARTITIONS_TOOL}, which is {partitions_status.status}"
            ownership_status = SourceStatus(
                partitions_status.status, OWNERSHIP_TOOL, detail
            )
        capacity_status, figures = self.capacity(candidate.entry)
        resource = candidate.entry.get("Resource") or {}
        system = InventorySystem(
            id=system_id,
            selector=candidate.selector,
            uuid=candidate.uuid,
            name=_text(resource.get("SystemName")),
            state=_text(resource.get("State")),
            total_memory_mib=figures.total_memory_mib if figures else None,
            free_memory_mib=figures.free_memory_mib if figures else None,
            total_proc_units=figures.total_proc_units if figures else None,
            free_proc_units=figures.free_proc_units if figures else None,
            sources=SystemSources(
                capacity=capacity_status,
                partitions=partitions_status,
                ownership=ownership_status,
            ),
        )
        return system, self._matching(entries, system_id, ownership_status, after), stop

    def _matching(
        self,
        entries: list[dict[str, Any]],
        system_id: str,
        ownership: SourceStatus,
        after: str | None,
    ) -> list[InventoryPartition]:
        ownership_ok = ownership.status == "ok"
        if self.owner is not None and not ownership_ok:
            return []
        found = [
            _partition(entry, system_id, ownership_ok)
            for entry in entries
            if entry.get("UUID") and (after is None or str(entry["UUID"]) > after)
        ]
        return sorted(
            (
                partition
                for partition in found
                if (self.lpar_state is None or partition.state == self.lpar_state)
                and (self.owner is None or partition.owner == self.owner)
            ),
            key=lambda partition: partition.uuid,
        )


async def read_inventory(
    hmc: Any,
    *,
    connection: str,
    admit: Admit,
    systems: Sequence[str] | None,
    lpar_state: PartitionState | None,
    owner: str | None,
    limit: int,
    cursor: str | None,
) -> InventoryPage:
    """Read one page of the connection's systems and partitions (ADR 0196)."""
    _check_inputs(systems, owner, limit)
    start = decode_cursor(cursor) if cursor is not None else None
    if systems is None:
        systems_source, candidates = await _enumerate(hmc, admit)
        unresolved: list[InventorySystem] = []
    else:
        systems_source = None
        unresolved, candidates = await _resolve(hmc, admit, systems)
    ordered = sorted(
        {c.uuid: c for c in reversed(candidates)}.values(), key=lambda c: c.uuid
    )
    if start is not None:
        ordered = [c for c in ordered if c.uuid >= start[0]]
    reader = _Reader(hmc, admit, connection, systems is None, lpar_state, owner)
    page_systems = list(unresolved) if start is None else []
    partitions: list[InventoryPartition] = []
    next_cursor: str | None = None
    systems_truncated = False
    for index, candidate in enumerate(ordered):
        if index == SYSTEMS_PER_PAGE:
            next_cursor, systems_truncated = encode_cursor(candidate.uuid, None), True
            break
        if len(partitions) == limit:
            next_cursor = encode_cursor(candidate.uuid, None)
            break
        after = start[1] if start is not None and candidate.uuid == start[0] else None
        system, found, stop = await reader.system(candidate, after)
        page_systems.append(system)
        room = limit - len(partitions)
        partitions.extend(found[:room])
        if len(found) > room:
            next_cursor = encode_cursor(candidate.uuid, partitions[-1].uuid)
            break
        if stop is not None:
            remaining = ordered[index + 1 :]
            next_cursor = encode_cursor(remaining[0].uuid, None) if remaining else None
            break
    return InventoryPage(
        connection=connection,
        systems_source=systems_source,
        systems=page_systems,
        systems_limit=SYSTEMS_PER_PAGE,
        systems_truncated=systems_truncated,
        partitions=partitions,
        limit=limit,
        truncated=next_cursor is not None,
        next_cursor=next_cursor,
    )
