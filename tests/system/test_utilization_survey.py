"""Contracts for the fleet utilization survey (ADR 0184).

Element nesting follows the read-only captures of 2026-10-01 (V10R3 and V11R2); every
name, serial and UUID here is synthetic.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from conftest import assert_no_mutating_requests, make_config

from hmcpctl.client.core import HMCClient
from hmcpctl.errors import HMCError
from hmcpctl.operations.inventory.utilization import (
    ProfileFailure,
    SystemReading,
    fleet_systems,
    read_system,
    rollup,
    survey_fleet,
    survey_hmc,
    utilization_pct,
)
from hmcpctl.xmlutil import parse_feed

_NS = 'xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/"'
SYSTEM_UUID = "11111111-2222-3333-4444-555555555555"
NEVER_UUID = "aaaaaaaa-0000-0000-0000-000000000003"
PROFILE_UUID = "bbbbbbbb-0000-0000-0000-000000000001"


def _entry(uuid: str, element: str, body: str) -> str:
    return (
        f"<entry><id>urn:uuid:{uuid}</id>"
        '<content type="application/vnd.ibm.powervm.uom+xml">'
        f"<{element} {_NS}>{body}</{element}></content></entry>"
    )


def _feed(*entries: str) -> str:
    return '<feed xmlns="http://www.w3.org/2005/Atom">' + "".join(entries) + "</feed>"


def _system(
    name: str = "system-a",
    serial: str = "SER0001",
    omit: tuple[str, ...] = (),
) -> str:
    memory = {
        "ConfigurableSystemMemory": "1000000",
        "CurrentAvailableSystemMemory": "600000",
        "InstalledSystemMemory": "1048576",
        "MemoryUsedByHypervisor": "40000",
        "CurrentAssignedMemoryToPartitions": "350000",
    }
    processors = {
        "ConfigurableSystemProcessorUnits": "48",
        "CurrentAvailableSystemProcessorUnits": "30",
        "InstalledSystemProcessorUnits": "48",
    }
    mem = "".join(f"<{k}>{v}</{k}>" for k, v in memory.items() if k not in omit)
    proc = "".join(f"<{k}>{v}</{k}>" for k, v in processors.items() if k not in omit)
    return _entry(
        SYSTEM_UUID,
        "ManagedSystem",
        f"<SystemName>{name}</SystemName><State>operating</State>"
        "<SystemFirmware>FW1120.00 (1)</SystemFirmware>"
        "<MachineTypeModelAndSerialNumber><MachineType>9080</MachineType>"
        f"<Model>HEX</Model><SerialNumber>{serial}</SerialNumber>"
        "</MachineTypeModelAndSerialNumber>"
        f"<AssociatedSystemMemoryConfiguration>{mem}"
        "</AssociatedSystemMemoryConfiguration>"
        f"<AssociatedSystemProcessorConfiguration>{proc}"
        "</AssociatedSystemProcessorConfiguration>",
    )


def _partition(
    uuid: str,
    state: str,
    memory: int,
    units: float,
    *,
    dedicated: bool = True,
    pool: int = 0,
    element: str = "LogicalPartition",
    profile_uuid: str | None = None,
) -> str:
    if dedicated:
        processors = (
            "<CurrentHasDedicatedProcessors>true</CurrentHasDedicatedProcessors>"
            "<CurrentDedicatedProcessorConfiguration>"
            f"<CurrentProcessors>{units:g}</CurrentProcessors>"
            "</CurrentDedicatedProcessorConfiguration>"
        )
    else:
        processors = (
            "<CurrentHasDedicatedProcessors>false</CurrentHasDedicatedProcessors>"
            "<CurrentSharedProcessorConfiguration>"
            f"<CurrentProcessingUnits>{units:g}</CurrentProcessingUnits>"
            f"<CurrentSharedProcessorPoolID>{pool}</CurrentSharedProcessorPoolID>"
            "</CurrentSharedProcessorConfiguration>"
        )
    link = (
        '<AssociatedPartitionProfile href="https://hmc.test/rest/api/uom/'
        f"LogicalPartition/{uuid}/LogicalPartitionProfile/{profile_uuid}"
        '" rel="related"/>'
        if profile_uuid
        else ""
    )
    return _entry(
        uuid,
        element,
        f"<PartitionState>{state}</PartitionState>{link}"
        "<PartitionMemoryConfiguration>"
        f"<CurrentMemory>{memory}</CurrentMemory><RuntimeMemory>256</RuntimeMemory>"
        f"</PartitionMemoryConfiguration><PartitionProcessorConfiguration>"
        f"{processors}</PartitionProcessorConfiguration>",
    )


def _profile(uuid: str, memory: int, units: float, *, dedicated: bool) -> str:
    if dedicated:
        processors = (
            "<HasDedicatedProcessors>true</HasDedicatedProcessors>"
            "<DedicatedProcessorConfiguration>"
            f"<DesiredProcessors>{units:g}</DesiredProcessors>"
            "</DedicatedProcessorConfiguration>"
        )
    else:
        processors = (
            "<HasDedicatedProcessors>false</HasDedicatedProcessors>"
            "<SharedProcessorConfiguration>"
            f"<DesiredProcessingUnits>{units:g}</DesiredProcessingUnits>"
            "</SharedProcessorConfiguration>"
        )
    return _entry(
        uuid,
        "LogicalPartitionProfile",
        f"<ProfileMemory><DesiredMemory>{memory}</DesiredMemory></ProfileMemory>"
        f"<ProcessorAttributes>{processors}</ProcessorAttributes>",
    )


LPARS = _feed(
    _partition("aaaaaaaa-0000-0000-0000-000000000001", "running", 200000, 8),
    _partition(
        "aaaaaaaa-0000-0000-0000-000000000002",
        "open firmware",
        50000,
        1.5,
        dedicated=False,
        pool=3,
    ),
    _partition("aaaaaaaa-0000-0000-0000-000000000004", "not activated", 80000, 4),
    _partition(
        NEVER_UUID, "not activated", 0, 0, dedicated=False, profile_uuid=PROFILE_UUID
    ),
)
VIOS = _feed(
    _partition(
        "cccccccc-0000-0000-0000-000000000001",
        "running",
        20000,
        2,
        element="VirtualIOServer",
    )
)
PROFILES = _feed(
    _profile("bbbbbbbb-0000-0000-0000-000000000009", 1, 1, dedicated=True),
    _profile(PROFILE_UUID, 16384, 0.5, dedicated=False),
)


class FakeClient:
    """An HMC answering parsed feeds; a value that is an exception is raised."""

    def __init__(self, **answers: Any) -> None:
        self.answers = {
            "systems": parse_feed(_feed(_system())),
            "lpars": parse_feed(LPARS),
            "vios": parse_feed(VIOS),
            "profiles": parse_feed(PROFILES),
        }
        self.answers.update(answers)

    def _answer(self, key: str) -> list[dict[str, Any]]:
        value = self.answers[key]
        if isinstance(value, Exception):
            raise value
        return value

    async def list_managed_systems(self) -> list[dict[str, Any]]:
        return self._answer("systems")

    async def list_logical_partitions(self, system_uuid=None):
        return self._answer("lpars")

    async def list_vios(self, system_uuid=None):
        return self._answer("vios")

    async def list_child(self, parent_type, parent_uuid, child_type):
        return self._answer("profiles")


async def _read(client: FakeClient) -> SystemReading:
    system = (await client.list_managed_systems())[0]
    return await read_system(client, "hmc-1", system)


@pytest.mark.asyncio
async def test_read_system_splits_allocation_per_adr_0184() -> None:
    reading = await _read(FakeClient())

    assert (reading.name, reading.machine_type, reading.model, reading.serial) == (
        "system-a",
        "9080",
        "HEX",
        "SER0001",
    )
    assert (reading.firmware, reading.state) == ("FW1120.00 (1)", "operating")
    memory = reading.memory
    assert (memory.installed, memory.configurable, memory.free) == (
        1048576,
        1000000,
        600000,
    )
    assert (memory.hypervisor, memory.vios) == (40000, 20000)
    assert (memory.client_active, memory.idle_reserved) == (250000, 80000)
    assert memory.other_reserved == 1000000 - 600000 - 40000 - 350000
    cpu = reading.cpu
    assert (cpu.vios, cpu.client_active, cpu.idle_reserved) == (2, 9.5, 4)
    assert cpu.other_reserved == 48 - 30 - 2 - 9.5 - 4
    assert (cpu.dedicated, cpu.shared) == (14, 1.5)
    assert reading.shared_pools == (3,)
    partitions = reading.partitions
    assert (partitions.running, partitions.not_activated, partitions.other) == (
        1,
        2,
        1,
    )
    assert reading.gaps == ()


@pytest.mark.asyncio
async def test_never_applied_partition_reports_profile_claim() -> None:
    reading = await _read(FakeClient())

    assert reading.partitions.profile_claims == 1
    assert reading.partitions.profile_claim_memory == 16384
    assert reading.partitions.profile_claim_units == 0.5
    assert reading.memory.idle_reserved == 80000


@pytest.mark.asyncio
async def test_failed_vios_feed_leaves_only_vios_dependent_figures_unknown() -> None:
    failure = HMCError("GET VirtualIOServer failed", 500)
    reading = await _read(FakeClient(vios=failure))

    assert reading.memory.vios is None
    assert reading.cpu.vios is None
    assert reading.cpu.other_reserved is None
    assert (reading.cpu.dedicated, reading.cpu.shared) == (None, None)
    assert reading.memory.other_reserved == 10000
    assert reading.memory.client_active == 250000
    assert reading.gaps == (
        "VirtualIOServer feed: GET VirtualIOServer failed (HTTP 500)",
    )


@pytest.mark.asyncio
async def test_failed_profile_read_is_a_gap() -> None:
    reading = await _read(FakeClient(profiles=HMCError("GET profile failed", 404)))

    assert reading.partitions.profile_claims == 1
    assert reading.partitions.profile_claim_memory is None
    assert reading.gaps == (
        "LogicalPartitionProfile feed: GET profile failed (HTTP 404)",
    )


@pytest.mark.asyncio
async def test_unreadable_profile_claim_names_the_reason() -> None:
    unlinked = parse_feed(_feed(_partition(NEVER_UUID, "not activated", 0, 0)))
    reading = await _read(FakeClient(lpars=unlinked))

    assert reading.partitions.profile_claim_memory is None
    assert reading.gaps == (
        (
            f"LogicalPartitionProfile: partition {NEVER_UUID} has no "
            "AssociatedPartitionProfile link"
        ),
    )
    other = _profile("bbbbbbbb-0000-0000-0000-000000000009", 1, 1, dedicated=True)
    reading = await _read(FakeClient(profiles=parse_feed(_feed(other))))

    assert reading.gaps == (
        (
            f"LogicalPartitionProfile: partition {NEVER_UUID}'s linked profile is not "
            "in its feed"
        ),
    )


@pytest.mark.asyncio
async def test_missing_system_figure_is_unknown_not_zero() -> None:
    systems = parse_feed(_feed(_system(omit=("MemoryUsedByHypervisor",))))
    reading = await _read(FakeClient(systems=systems))

    assert reading.memory.hypervisor is None
    assert reading.memory.other_reserved is None
    assert reading.unknown_figures == 2


@asynccontextmanager
async def _opened(client: Any):
    yield client


class StalledClient(FakeClient):
    """Logs on, then never answers the partition feed."""

    async def list_logical_partitions(self, system_uuid=None):
        await asyncio.sleep(60)
        return []


@pytest.mark.asyncio
async def test_survey_fleet_names_each_failed_profile() -> None:
    def open_client(profile: str):
        if profile == "bad-config":
            raise ValueError("profile 'bad-config' has no host")
        if profile == "refused":
            return _opened(FakeClient(systems=HMCError("logon failed", 401)))
        if profile == "stalled":
            return _opened(StalledClient())
        if profile == "slow":

            @asynccontextmanager
            async def hang():
                await asyncio.sleep(60)
                yield FakeClient()

            return hang()
        return _opened(FakeClient())

    survey = await survey_fleet(
        ["ok", "bad-config", "refused", "slow", "stalled", "ok"],
        open_client,
        hmc_timeout=0.2,
    )

    assert survey.profiles == ("ok", "bad-config", "refused", "slow", "stalled")
    assert [reading.profile for reading in survey.readings] == ["ok"]
    assert survey.failures == (
        ProfileFailure("bad-config", "ValueError: profile 'bad-config' has no host"),
        ProfileFailure("refused", "HMCError: logon failed (HTTP 401)"),
        ProfileFailure("slow", "no answer within 0.2 s"),
        ProfileFailure("stalled", "no answer within 0.2 s"),
    )


@pytest.mark.asyncio
async def test_survey_fleet_bounds_concurrency() -> None:
    active = 0
    peak = 0

    @asynccontextmanager
    async def open_client(profile: str):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        try:
            yield FakeClient()
        finally:
            active -= 1

    survey = await survey_fleet([f"p{i}" for i in range(6)], open_client, concurrency=2)

    assert peak == 2
    assert len(survey.readings) == 6


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("concurrency", "timeout", "message"),
    [(0, 1.0, "concurrency"), (1, 0.0, "hmc_timeout")],
)
async def test_survey_fleet_rejects_invalid_bounds(
    concurrency, timeout, message
) -> None:
    with pytest.raises(ValueError, match=message):
        await survey_fleet(["p"], _opened, concurrency=concurrency, hmc_timeout=timeout)


@pytest.mark.asyncio
async def test_fleet_systems_dedups_by_mtms_preferring_complete_reading() -> None:
    complete = await _read(FakeClient())
    partial = await read_system(
        FakeClient(vios=HMCError("down", 500)),
        "hmc-0",
        parse_feed(_feed(_system()))[0],
    )
    other = await read_system(
        FakeClient(), "hmc-0", parse_feed(_feed(_system("system-b", "SER0002")))[0]
    )

    systems = fleet_systems([partial, complete, other])

    assert [system.reading.name for system in systems] == ["system-a", "system-b"]
    assert systems[0].reading is complete
    assert systems[0].profiles == ("hmc-0", "hmc-1")
    twin = await read_system(FakeClient(), "hmc-0", parse_feed(_feed(_system()))[0])
    assert fleet_systems([complete, twin])[0].reading is twin


@pytest.mark.asyncio
async def test_rollup_sums_each_figure_and_names_shortfalls() -> None:
    complete = await _read(FakeClient())
    partial = await _read(FakeClient(vios=HMCError("down", 500)))

    total = rollup([complete, complete, partial])

    assert total.systems == 3
    assert total.memory.configurable == 3000000
    assert total.memory.vios == 40000
    assert total.memory.other_reserved == 30000
    assert total.cpu.client_active == 28.5
    assert total.cpu.other_reserved == 5.0
    assert total.partitions.profile_claim_memory == 49152
    assert (total.cpu_util_pct, total.mem_util_pct) == (37.5, 40.0)
    assert (total.cpu_allocated, total.mem_allocated) == (54.0, 1200000)
    assert total.unknown == (
        ("cpu", "vios", 1),
        ("cpu", "other_reserved", 1),
        ("cpu", "dedicated", 1),
        ("cpu", "shared", 1),
        ("memory", "vios", 1),
    )
    empty = rollup([])
    assert (empty.systems, empty.memory.free, empty.mem_util_pct) == (0, None, None)
    assert empty.unknown == ()


@pytest.mark.asyncio
async def test_rollup_utilization_skips_systems_missing_a_capacity_figure() -> None:
    complete = await _read(FakeClient())
    systems = parse_feed(_feed(_system(omit=("CurrentAvailableSystemMemory",))))
    no_free = await _read(FakeClient(systems=systems))

    total = rollup([complete, no_free])

    assert (total.mem_allocated, total.mem_util_pct) == (400000, 40.0)
    assert ("memory", "free", 1) in total.unknown


def test_utilization_pct() -> None:
    assert utilization_pct(48, 30) == 37.5
    assert utilization_pct(None, 30) is None
    assert utilization_pct(48, None) is None
    assert utilization_pct(0, 0) is None


@pytest.mark.asyncio
async def test_survey_hmc_through_client_issues_only_gets(mock_hmc) -> None:
    def ok(text: str) -> httpx.Response:
        return httpx.Response(200, text=text)

    base = f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}"
    mock_hmc.get("/rest/api/uom/ManagedSystem").mock(return_value=ok(_feed(_system())))
    mock_hmc.get(f"{base}/LogicalPartition").mock(return_value=ok(LPARS))
    mock_hmc.get(f"{base}/VirtualIOServer").mock(
        return_value=httpx.Response(500, text="ViosStorage")
    )
    mock_hmc.get(
        f"/rest/api/uom/LogicalPartition/{NEVER_UUID}/LogicalPartitionProfile"
    ).mock(return_value=ok(PROFILES))

    async with HMCClient(make_config()) as hmc:
        readings = await survey_hmc(hmc, "hmc-1")

    assert [reading.name for reading in readings] == ["system-a"]
    assert readings[0].partitions.profile_claim_memory == 16384
    assert readings[0].memory.vios is None
    assert readings[0].gaps[0].startswith("VirtualIOServer feed:")
    assert_no_mutating_requests(mock_hmc)
