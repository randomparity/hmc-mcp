"""Direct contracts for capacity parsing and placement ordering."""

import pytest
from conftest import captured_lpar_entry, captured_system_entry

from hmcpctl.operations.inventory.capacity import (
    calculate_system_capacity,
    find_placement,
)
from hmcpctl.operations.inventory.composite import _system_summary
from hmcpctl.xmlutil import parse_feed


def _parsed(entry: str) -> dict:
    feed = f'<feed xmlns="http://www.w3.org/2005/Atom">\n{entry}\n</feed>'
    return parse_feed(feed)[0]


SYSTEM = _parsed(captured_system_entry("system-1", "system-1"))
INACTIVE_LPAR = _parsed(captured_lpar_entry("lpar-1", "aix-1"))


def test_capacity_reads_the_captured_configuration_containers():
    summary = calculate_system_capacity(SYSTEM, [INACTIVE_LPAR])

    assert summary.total_memory_mib == 131072
    assert summary.free_memory_mib == 112448
    assert summary.assigned_memory_mib == 131072 - 112448
    assert summary.total_proc_units == 20.0
    assert summary.free_proc_units == 18.0
    assert summary.assigned_proc_units == 2.0
    assert summary.total_lpars == 1
    assert summary.running_lpars == 0


@pytest.mark.parametrize(
    "summarize",
    [
        lambda system: calculate_system_capacity(system, []),
        lambda system: _system_summary(system, [], []),
    ],
)
@pytest.mark.parametrize(
    ("container", "field"),
    [
        ("AssociatedSystemMemoryConfiguration", "ConfigurableSystemMemory"),
        ("AssociatedSystemProcessorConfiguration", "ConfigurableSystemProcessorUnits"),
    ],
)
def test_missing_capacity_container_fails_instead_of_reading_zero(
    summarize, container, field
):
    system = _parsed(captured_system_entry("system-1", "system-1", omit=(container,)))

    with pytest.raises(ValueError, match=rf"system-1.*{container}/{field}.*unknown"):
        summarize(system)


@pytest.mark.parametrize(
    ("field", "overrides"),
    [
        ("ConfigurableSystemMemory", {"configurable_mem": "not-memory"}),
        ("CurrentAvailableSystemMemory", {"available_mem": "1.5"}),
        ("ConfigurableSystemProcessorUnits", {"configurable_proc": "many"}),
        ("CurrentAvailableSystemProcessorUnits", {"available_proc": ""}),
    ],
)
def test_capacity_summaries_contextualize_malformed_figures(field, overrides):
    system = _parsed(captured_system_entry("system-1", "system-1", **overrides))

    with pytest.raises(ValueError, match=rf"system-1.*{field}"):
        calculate_system_capacity(system, [])


def test_capacity_reads_a_figure_carrying_an_attribute():
    system = _parsed(captured_system_entry("system-1", "system-1"))
    memory = system["Resource"]["AssociatedSystemMemoryConfiguration"]
    memory["CurrentAvailableSystemMemory"] = {"@attrs": {"ksv": "V1_0"}, "text": "4096"}

    assert calculate_system_capacity(system, []).free_memory_mib == 4096


class _CapacityClient:
    async def list_managed_systems(self):
        return [
            _parsed(
                captured_system_entry(
                    "roomy", "roomy", available_mem="32768", available_proc="12"
                )
            ),
            _parsed(
                captured_system_entry(
                    "tight", "tight", available_mem="8192", available_proc="4"
                )
            ),
        ]

    async def list_logical_partitions(self, _system_uuid):
        return []


@pytest.mark.asyncio
async def test_find_placement_orders_smallest_sufficient_capacity_first():
    result = await find_placement(
        _CapacityClient(), desired_memory_mib=4096, desired_proc_units=1
    )

    assert [candidate.system_uuid for candidate in result] == ["tight", "roomy"]
