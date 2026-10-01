"""Presentation-neutral fleet utilization survey and roll-ups (ADR 0184).

Allocation is read from partitions' current configurations: the hypervisor reserves
a not-activated partition's current configuration, so runtime figures under-report.
Every figure is ``None`` when the HMC did not report it or a read failed; nothing
defaults to zero.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, fields
from typing import Any, Protocol, TypeVar

from hmcpctl.errors import HMCError
from hmcpctl.xmlutil import leaf_text, mtms_parts

_logger = logging.getLogger(__name__)

_NOT_ACTIVATED = "not activated"
_RUNNING = "running"
_SYSTEM_MEMORY = "AssociatedSystemMemoryConfiguration"
_SYSTEM_PROCESSORS = "AssociatedSystemProcessorConfiguration"
_MEMORY = "PartitionMemoryConfiguration"
_PROCESSORS = "PartitionProcessorConfiguration"
_Figures = TypeVar("_Figures", "CpuFigures", "MemoryFigures", "PartitionFigures")


class SurveyClient(Protocol):
    """The HMC reads a survey makes; ``HMCClient`` satisfies it."""

    async def list_managed_systems(self) -> list[dict[str, Any]]: ...

    async def list_logical_partitions(
        self, system_uuid: str | None = None
    ) -> list[dict[str, Any]]: ...

    async def list_vios(
        self, system_uuid: str | None = None
    ) -> list[dict[str, Any]]: ...

    async def list_child(
        self, parent_type: str, parent_uuid: str, child_type: str
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class CpuFigures:
    """Processor units of one system, or their sum over a roll-up."""

    installed: float | None
    configurable: float | None
    vios: float | None
    client_active: float | None
    idle_reserved: float | None
    other_reserved: float | None
    free: float | None
    dedicated: float | None
    shared: float | None


@dataclass(frozen=True)
class MemoryFigures:
    """Memory in MiB of one system, or its sum over a roll-up."""

    installed: int | None
    configurable: int | None
    hypervisor: int | None
    vios: int | None
    client_active: int | None
    idle_reserved: int | None
    other_reserved: int | None
    free: int | None


@dataclass(frozen=True)
class PartitionFigures:
    """Client partition counts by state, and never-applied partitions' claims."""

    running: int | None
    not_activated: int | None
    other: int | None
    profile_claims: int | None
    profile_claim_memory: int | None
    profile_claim_units: float | None


@dataclass(frozen=True)
class SystemReading:
    """One managed system as one profile's HMC reported it."""

    profile: str
    name: str
    machine_type: str | None
    model: str | None
    serial: str | None
    firmware: str | None
    state: str | None
    cpu: CpuFigures
    memory: MemoryFigures
    partitions: PartitionFigures
    shared_pools: tuple[int, ...]
    gaps: tuple[str, ...]

    @property
    def unknown_figures(self) -> int:
        """How many CPU, memory and partition figures are unknown."""
        return sum(
            getattr(group, item.name) is None
            for group in (self.cpu, self.memory, self.partitions)
            for item in fields(group)
        )


@dataclass(frozen=True)
class ProfileFailure:
    """A profile that could not be surveyed, and why."""

    profile: str
    reason: str


@dataclass(frozen=True)
class FleetSurvey:
    """Every surveyed profile's readings, and the profiles that failed."""

    profiles: tuple[str, ...]
    readings: tuple[SystemReading, ...]
    failures: tuple[ProfileFailure, ...]


@dataclass(frozen=True)
class FleetSystem:
    """One physical system: its chosen reading and every profile managing it."""

    reading: SystemReading
    profiles: tuple[str, ...]


@dataclass(frozen=True)
class Rollup:
    """Per-figure sums over ``systems`` readings, and each figure's shortfall.

    ``unknown`` holds ``(group, figure, count)`` for every figure ``count`` of the
    readings lack; ``group`` is ``cpu``, ``memory`` or ``partitions``.
    """

    systems: int
    cpu: CpuFigures
    memory: MemoryFigures
    partitions: PartitionFigures
    cpu_allocated: float | None
    cpu_util_pct: float | None
    mem_allocated: int | None
    mem_util_pct: float | None
    unknown: tuple[tuple[str, str, int], ...]


@dataclass(frozen=True)
class _Size:
    memory: int | None
    units: float | None
    dedicated: bool | None
    pool: int | None


def _leaf(container: object, *path: str) -> str | None:
    value = container
    for name in path:
        if not isinstance(value, dict):
            return None
        value = value.get(name)
    text = leaf_text(value)
    return text.strip() if isinstance(text, str) else None


def _int(text: str | None) -> int | None:
    try:
        return None if text is None else int(text)
    except ValueError:
        return None


def _float(text: str | None) -> float | None:
    try:
        return None if text is None else float(text)
    except ValueError:
        return None


def _total(values: Iterable[Any]) -> Any:
    total: Any = 0
    for value in values:
        if value is None:
            return None
        total += value
    return round(total, 4) if isinstance(total, float) else total


def _less(first: Any, *rest: Any) -> Any:
    taken = _total(rest)
    if first is None or taken is None:
        return None
    left = first - taken
    return round(left, 4) if isinstance(left, float) else left


def _current_size(resource: dict[str, Any]) -> _Size:
    memory = _int(_leaf(resource, _MEMORY, "CurrentMemory"))
    flag = _leaf(resource, _PROCESSORS, "CurrentHasDedicatedProcessors")
    if flag == "true":
        dedicated = (_PROCESSORS, "CurrentDedicatedProcessorConfiguration")
        units = _float(_leaf(resource, *dedicated, "CurrentProcessors"))
        return _Size(memory, units, True, None)
    if flag == "false":
        shared = (_PROCESSORS, "CurrentSharedProcessorConfiguration")
        units = _float(_leaf(resource, *shared, "CurrentProcessingUnits"))
        pool = _int(_leaf(resource, *shared, "CurrentSharedProcessorPoolID"))
        return _Size(memory, units, False, pool)
    return _Size(memory, None, None, None)


def _never_applied(state: str | None, size: _Size) -> bool:
    return state == _NOT_ACTIVATED and size.memory == 0 and size.units == 0


async def _read_feed(
    gaps: list[str],
    label: str,
    read: Callable[[str], Any],
    uuid: object,
) -> list[dict[str, Any]] | None:
    if not isinstance(uuid, str):
        gaps.append(f"{label} feed: the system has no UUID")
        return None
    try:
        return await read(uuid)
    except HMCError as exc:
        gaps.append(f"{label} feed: {exc}")
        return None


async def _profile_claim(
    hmc: SurveyClient, partition: dict[str, Any], gaps: list[str]
) -> tuple[int | None, float | None]:
    uuid = partition.get("UUID")
    link = (partition.get("Resource") or {}).get("AssociatedPartitionProfile")
    href = link.get("href") if isinstance(link, dict) else None
    if not isinstance(uuid, str) or not isinstance(href, str):
        gaps.append(
            f"LogicalPartitionProfile: partition {uuid} has no "
            "AssociatedPartitionProfile link"
        )
        return None, None
    wanted = href.rstrip("/").rsplit("/", 1)[-1].casefold()
    for profile in await hmc.list_child(
        "LogicalPartition", uuid, "LogicalPartitionProfile"
    ):
        if str(profile.get("UUID") or "").casefold() != wanted:
            continue
        resource = profile.get("Resource") or {}
        memory = _int(_leaf(resource, "ProfileMemory", "DesiredMemory"))
        flag = _leaf(resource, "ProcessorAttributes", "HasDedicatedProcessors")
        if flag == "true":
            path = ("DedicatedProcessorConfiguration", "DesiredProcessors")
        elif flag == "false":
            path = ("SharedProcessorConfiguration", "DesiredProcessingUnits")
        else:
            gaps.append(
                f"LogicalPartitionProfile: the profile of partition {uuid} has no "
                "HasDedicatedProcessors"
            )
            return memory, None
        return memory, _float(_leaf(resource, "ProcessorAttributes", *path))
    gaps.append(
        f"LogicalPartitionProfile: partition {uuid}'s linked profile is not in its feed"
    )
    return None, None


async def _partition_figures(
    hmc: SurveyClient,
    gaps: list[str],
    lpars: list[dict[str, Any]] | None,
) -> tuple[PartitionFigures, list[tuple[str | None, _Size]]]:
    if lpars is None:
        unknown = PartitionFigures(None, None, None, None, None, None)
        return unknown, []
    states = [
        (
            (lpar.get("Resource") or {}).get("PartitionState"),
            _current_size(lpar.get("Resource") or {}),
        )
        for lpar in lpars
    ]
    claims: list[tuple[int | None, float | None]] = []
    for lpar, (state, size) in zip(lpars, states, strict=True):
        if not _never_applied(state, size):
            continue
        try:
            claims.append(await _profile_claim(hmc, lpar, gaps))
        except HMCError as exc:
            gaps.append(f"LogicalPartitionProfile feed: {exc}")
            claims.append((None, None))
    running = sum(state == _RUNNING for state, _ in states)
    not_activated = sum(state == _NOT_ACTIVATED for state, _ in states)
    figures = PartitionFigures(
        running=running,
        not_activated=not_activated,
        other=len(states) - running - not_activated,
        profile_claims=len(claims),
        profile_claim_memory=_total(memory for memory, _ in claims),
        profile_claim_units=_total(units for _, units in claims),
    )
    return figures, states


def _split(
    states: list[tuple[str | None, _Size]], lpars_known: bool
) -> tuple[list[_Size], list[_Size]]:
    if not lpars_known:
        return [], []
    active = [size for state, size in states if state != _NOT_ACTIVATED]
    idle = [
        size
        for state, size in states
        if state == _NOT_ACTIVATED and not _never_applied(state, size)
    ]
    return active, idle


def _sum_or_unknown(sizes: list[_Size], known: bool, attribute: str) -> Any:
    return _total(getattr(size, attribute) for size in sizes) if known else None


async def read_system(
    hmc: SurveyClient, profile: str, system: dict[str, Any]
) -> SystemReading:
    """Read one managed system's allocation per ADR 0184."""
    resource = system.get("Resource") or {}
    uuid = system.get("UUID")
    gaps: list[str] = []
    lpars = await _read_feed(
        gaps, "LogicalPartition", hmc.list_logical_partitions, uuid
    )
    vios = await _read_feed(gaps, "VirtualIOServer", hmc.list_vios, uuid)
    partitions, states = await _partition_figures(hmc, gaps, lpars)
    active, idle = _split(states, lpars is not None)
    vios_sizes = [_current_size(entry.get("Resource") or {}) for entry in vios or []]
    every = [size for _, size in states] + vios_sizes
    both = lpars is not None and vios is not None
    flags_known = both and all(size.dedicated is not None for size in every)

    memory_configurable = _int(
        _leaf(resource, _SYSTEM_MEMORY, "ConfigurableSystemMemory")
    )
    memory_free = _int(_leaf(resource, _SYSTEM_MEMORY, "CurrentAvailableSystemMemory"))
    hypervisor = _int(_leaf(resource, _SYSTEM_MEMORY, "MemoryUsedByHypervisor"))
    assigned = _int(
        _leaf(resource, _SYSTEM_MEMORY, "CurrentAssignedMemoryToPartitions")
    )
    memory = MemoryFigures(
        installed=_int(_leaf(resource, _SYSTEM_MEMORY, "InstalledSystemMemory")),
        configurable=memory_configurable,
        hypervisor=hypervisor,
        vios=_sum_or_unknown(vios_sizes, vios is not None, "memory"),
        client_active=_sum_or_unknown(active, lpars is not None, "memory"),
        idle_reserved=_sum_or_unknown(idle, lpars is not None, "memory"),
        other_reserved=_less(memory_configurable, memory_free, hypervisor, assigned),
        free=memory_free,
    )

    units_configurable = _float(
        _leaf(resource, _SYSTEM_PROCESSORS, "ConfigurableSystemProcessorUnits")
    )
    units_free = _float(
        _leaf(resource, _SYSTEM_PROCESSORS, "CurrentAvailableSystemProcessorUnits")
    )
    vios_units = _sum_or_unknown(vios_sizes, vios is not None, "units")
    active_units = _sum_or_unknown(active, lpars is not None, "units")
    idle_units = _sum_or_unknown(idle, lpars is not None, "units")
    cpu = CpuFigures(
        installed=_float(
            _leaf(resource, _SYSTEM_PROCESSORS, "InstalledSystemProcessorUnits")
        ),
        configurable=units_configurable,
        vios=vios_units,
        client_active=active_units,
        idle_reserved=idle_units,
        other_reserved=_less(
            units_configurable, units_free, vios_units, active_units, idle_units
        ),
        free=units_free,
        dedicated=_sum_or_unknown(
            [size for size in every if size.dedicated], flags_known, "units"
        ),
        shared=_sum_or_unknown(
            [size for size in every if size.dedicated is False], flags_known, "units"
        ),
    )
    pools = sorted(
        {
            size.pool
            for size in every
            if size.dedicated is False and size.pool is not None and size.units
        }
    )
    mtms = mtms_parts(resource)
    return SystemReading(
        profile=profile,
        name=_leaf(resource, "SystemName") or str(uuid or "unknown system"),
        machine_type=mtms[0] if mtms else None,
        model=mtms[1] if mtms else None,
        serial=mtms[2] if mtms else None,
        firmware=_leaf(resource, "SystemFirmware"),
        state=_leaf(resource, "State"),
        cpu=cpu,
        memory=memory,
        partitions=partitions,
        shared_pools=tuple(pools),
        gaps=tuple(gaps),
    )


async def survey_hmc(hmc: SurveyClient, profile: str) -> list[SystemReading]:
    """Read every managed system one profile's HMC reports, one at a time."""
    return [
        await read_system(hmc, profile, system)
        for system in await hmc.list_managed_systems()
    ]


async def survey_fleet(
    profiles: Sequence[str],
    open_client: Callable[[str], AbstractAsyncContextManager[SurveyClient]],
    *,
    concurrency: int = 4,
    hmc_timeout: float = 300.0,
) -> FleetSurvey:
    """Survey *profiles*, at most *concurrency* at once, *hmc_timeout* s each.

    A profile whose client, logon, reads or deadline fail becomes a
    ``ProfileFailure`` and contributes no readings. A failure closing the
    session after every read finished keeps the readings and logs a warning.
    """
    if concurrency < 1:
        raise ValueError(f"concurrency must be at least 1, got {concurrency}")
    if hmc_timeout <= 0:
        raise ValueError(f"hmc_timeout must be positive, got {hmc_timeout}")
    names = tuple(dict.fromkeys(profiles))
    gate = asyncio.Semaphore(concurrency)

    async def survey(profile: str) -> list[SystemReading] | ProfileFailure:
        readings: list[SystemReading] | None = None
        async with gate:
            try:
                async with asyncio.timeout(hmc_timeout):
                    async with open_client(profile) as hmc:
                        readings = await survey_hmc(hmc, profile)
            except TimeoutError:
                failure = f"no answer within {hmc_timeout:g} s"
            except Exception as exc:  # noqa: BLE001 - every profile failure is reported as a row, never dropped (ADR 0184)
                failure = f"{type(exc).__name__}: {exc}"
            else:
                return readings
        if readings is None:
            return ProfileFailure(profile, failure)
        _logger.warning(
            "%s: every read finished, but closing the session failed: %s",
            profile,
            failure,
        )
        return readings

    results = await asyncio.gather(*(survey(name) for name in names))
    readings = tuple(
        reading for result in results if isinstance(result, list) for reading in result
    )
    failures = tuple(result for result in results if isinstance(result, ProfileFailure))
    return FleetSurvey(names, readings, failures)


def fleet_systems(readings: Iterable[SystemReading]) -> tuple[FleetSystem, ...]:
    """Count each machine type-model-serial once, keeping its most complete reading."""
    groups: dict[tuple[object, ...], list[SystemReading]] = {}
    for index, reading in enumerate(readings):
        key: tuple[object, ...] = (
            ("mtms", reading.machine_type, reading.model, reading.serial)
            if reading.serial is not None
            else ("reading", index)
        )
        groups.setdefault(key, []).append(reading)
    systems = [
        FleetSystem(
            reading=min(group, key=lambda item: (item.unknown_figures, item.profile)),
            profiles=tuple(sorted({item.profile for item in group})),
        )
        for group in groups.values()
    ]
    return tuple(
        sorted(systems, key=lambda item: (item.reading.name, item.reading.serial or ""))
    )


def _sum_figures(
    label: str, kind: type[_Figures], groups: list[_Figures]
) -> tuple[_Figures, list[tuple[str, str, int]]]:
    sums: dict[str, Any] = {}
    unknown: list[tuple[str, str, int]] = []
    for item in fields(kind):
        values = [getattr(group, item.name) for group in groups]
        known = [value for value in values if value is not None]
        sums[item.name] = _total(known) if known else None
        if len(known) < len(values):
            unknown.append((label, item.name, len(values) - len(known)))
    return kind(**sums), unknown


def _pooled(pairs: Iterable[tuple[Any, Any]]) -> tuple[Any, float | None]:
    """Allocated capacity and its percentage over the readings reporting both figures."""
    known = [(total, free) for total, free in pairs if None not in (total, free)]
    if not known:
        return None, None
    total = _total(configurable for configurable, _ in known)
    free = _total(available for _, available in known)
    return allocated(total, free), utilization_pct(total, free)


def rollup(readings: Iterable[SystemReading]) -> Rollup:
    """Sum each figure over the readings that reported it (ADR 0184 decision 4)."""
    every = tuple(readings)
    cpu, cpu_unknown = _sum_figures("cpu", CpuFigures, [r.cpu for r in every])
    memory, memory_unknown = _sum_figures(
        "memory", MemoryFigures, [r.memory for r in every]
    )
    partitions, partitions_unknown = _sum_figures(
        "partitions", PartitionFigures, [r.partitions for r in every]
    )
    cpu_allocated, cpu_pct = _pooled((r.cpu.configurable, r.cpu.free) for r in every)
    mem_allocated, mem_pct = _pooled(
        (r.memory.configurable, r.memory.free) for r in every
    )
    return Rollup(
        systems=len(every),
        cpu=cpu,
        memory=memory,
        partitions=partitions,
        cpu_allocated=cpu_allocated,
        cpu_util_pct=cpu_pct,
        mem_allocated=mem_allocated,
        mem_util_pct=mem_pct,
        unknown=tuple(cpu_unknown + memory_unknown + partitions_unknown),
    )


def allocated(configurable: Any, free: Any) -> Any:
    """Configurable minus free capacity, or ``None`` when either is unknown."""
    return _less(configurable, free)


def utilization_pct(configurable: float | None, free: float | None) -> float | None:
    """Allocated share of configurable capacity, in percent to one decimal."""
    if configurable is None or free is None or configurable == 0:
        return None
    return round(100 * (configurable - free) / configurable, 1)
