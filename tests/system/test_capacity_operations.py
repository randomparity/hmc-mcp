"""Direct contracts for capacity parsing and placement ordering."""

import pytest

from hmc_mcp.operations.capacity import calculate_system_capacity, find_placement
from hmc_mcp.operations.composite import _system_summary

SYSTEM = {
    "UUID": "system-1",
    "Resource": {
        "SystemName": "system-1",
        "AssignableSystemMemory": "16384",
        "ConfigurableSystemProcessorUnits": "8.0",
    },
}
MALFORMED_LPAR = {
    "UUID": "lpar-1",
    "Resource": {
        "PartitionName": "bad-lpar",
        "DesiredMemory": "1024",
        "DesiredProcessingUnits": "not-a-number",
    },
}


@pytest.mark.parametrize(
    "summarize",
    [
        lambda: calculate_system_capacity(SYSTEM, [MALFORMED_LPAR]),
        lambda: _system_summary(SYSTEM, [MALFORMED_LPAR], []),
    ],
)
def test_capacity_summaries_reject_malformed_processing_units(summarize):
    with pytest.raises(
        ValueError,
        match=r"lpar-1.*DesiredProcessingUnits.*not-a-number",
    ):
        summarize()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("AssignableSystemMemory", "not-memory"),
        ("ConfigurableSystemProcessorUnits", "not-processors"),
        ("DesiredMemory", "not-memory"),
    ],
)
def test_capacity_summaries_contextualize_malformed_numeric_fields(field, value):
    system = {**SYSTEM, "Resource": {**SYSTEM["Resource"], field: value}}
    lpars = [] if field != "DesiredMemory" else [{"UUID": "lpar-2", "Resource": {field: value}}]

    with pytest.raises(ValueError, match=field):
        calculate_system_capacity(system, lpars)


class _CapacityClient:
    async def list_managed_systems(self):
        return [
            {
                "UUID": "roomy",
                "Resource": {
                    "SystemName": "roomy",
                    "AssignableSystemMemory": 32768,
                    "ConfigurableSystemProcessorUnits": 12,
                },
            },
            {
                "UUID": "tight",
                "Resource": {
                    "SystemName": "tight",
                    "AssignableSystemMemory": 8192,
                    "ConfigurableSystemProcessorUnits": 4,
                },
            },
        ]

    async def list_logical_partitions(self, _system_uuid):
        return []


@pytest.mark.asyncio
async def test_find_placement_orders_smallest_sufficient_capacity_first():
    result = await find_placement(
        _CapacityClient(), desired_memory_mib=4096, desired_proc_units=1
    )

    assert [candidate.system_uuid for candidate in result] == ["tight", "roomy"]
