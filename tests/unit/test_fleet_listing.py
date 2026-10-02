"""Unscoped listings read system by system and name what they skip (ADR 0197)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from hmcpctl.errors import HMCError
from hmcpctl.operations.systems.fleet import (
    FleetListing,
    UnreadableSystem,
    read_fleet,
)

R1_UUID = "11111111-1111-1111-1111-111111111111"
E2_UUID = "22222222-2222-2222-2222-222222222222"


def _system(uuid: str | None, name: str, state: object, detailed: str = "None"):
    resource = {"SystemName": name, "State": state, "DetailedState": detailed}
    return {"UUID": uuid, "Resource": resource} if uuid else {"Resource": resource}


def _hmc(systems, unresolved=()):
    hmc = AsyncMock()
    hmc.inventory_managed_systems.return_value = (systems, list(unresolved))
    return hmc


def _reader(by_uuid):
    async def read(uuid: str):
        result = by_uuid[uuid]
        if isinstance(result, Exception):
            raise result
        return result

    return AsyncMock(side_effect=read)


@pytest.mark.asyncio
async def test_skips_and_names_a_system_that_is_not_operating():
    hmc = _hmc(
        [
            _system(R1_UUID, "sys-R1", "No Connection", "Connecting"),
            _system(E2_UUID, "sys-E2", "operating"),
        ]
    )
    read = _reader({E2_UUID: [{"UUID": "lpar-1"}]})

    listing = await read_fleet(hmc, read, "LPARs")

    assert listing == FleetListing(
        [{"UUID": "lpar-1"}],
        [UnreadableSystem("sys-R1", R1_UUID, "No Connection", "Connecting")],
    )
    read.assert_awaited_once_with(E2_UUID)


@pytest.mark.asyncio
async def test_state_comparison_ignores_case_and_padding():
    hmc = _hmc([_system(E2_UUID, "sys-E2", " Operating ")])
    read = _reader({E2_UUID: [{"UUID": "lpar-1"}]})

    listing = await read_fleet(hmc, read, "LPARs")

    assert listing.unreadable_systems == []
    assert listing.entries == [{"UUID": "lpar-1"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [None, 7])
async def test_a_system_without_a_state_string_is_unreadable(state):
    hmc = _hmc([_system(E2_UUID, "sys-E2", state)])
    read = _reader({})

    listing = await read_fleet(hmc, read, "LPARs")

    assert listing.unreadable_systems == [
        UnreadableSystem("sys-E2", E2_UUID, None, "None")
    ]
    read.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_system_without_a_uuid_is_named_and_not_read():
    hmc = _hmc([_system(None, "sys-R1", "operating")])
    read = _reader({})

    listing = await read_fleet(hmc, read, "LPARs")

    assert listing.unreadable_systems == [
        UnreadableSystem("sys-R1", None, "operating", "None")
    ]
    read.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_system_the_inventory_could_not_resolve_is_named():
    hmc = _hmc([_system(E2_UUID, "sys-E2", "operating")], [(R1_UUID, "sys-R1")])
    read = _reader({E2_UUID: []})

    listing = await read_fleet(hmc, read, "VIOSes")

    assert listing.unreadable_systems == [
        UnreadableSystem("sys-R1", R1_UUID, None, None)
    ]


@pytest.mark.asyncio
async def test_a_fleet_over_the_discovery_bound_is_refused_before_any_read():
    systems = [
        _system(f"{index:08d}-0000-0000-0000-000000000000", f"sys-{index}", "operating")
        for index in range(101)
    ]
    read = _reader({})

    with pytest.raises(ValueError, match="supply managed-system scope"):
        await read_fleet(_hmc(systems), read, "LPARs")
    read.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_system_that_stops_operating_mid_read_is_named():
    hmc = _hmc(
        [
            _system(R1_UUID, "sys-R1", "operating"),
            _system(E2_UUID, "sys-E2", "operating"),
        ]
    )
    hmc.get_managed_system.return_value = _system(
        R1_UUID, "sys-R1", "No Connection", "Connecting"
    )
    read = _reader({R1_UUID: HMCError("refused", 204), E2_UUID: [{"UUID": "lpar-2"}]})

    listing = await read_fleet(hmc, read, "LPARs")

    assert listing == FleetListing(
        [{"UUID": "lpar-2"}],
        [UnreadableSystem("sys-R1", R1_UUID, "No Connection", "Connecting")],
    )


@pytest.mark.asyncio
async def test_an_error_from_a_system_still_operating_fails_the_listing():
    hmc = _hmc([_system(E2_UUID, "sys-E2", "operating")])
    hmc.get_managed_system.return_value = _system(E2_UUID, "sys-E2", "operating")
    error = HMCError("server error", 500)

    with pytest.raises(HMCError) as raised:
        await read_fleet(hmc, _reader({E2_UUID: error}), "LPARs")
    assert raised.value is error


@pytest.mark.asyncio
async def test_a_failed_state_reread_keeps_the_original_error():
    hmc = _hmc([_system(E2_UUID, "sys-E2", "operating")])
    hmc.get_managed_system.side_effect = HMCError("also down", 500)
    error = HMCError("server error", 500)

    with pytest.raises(HMCError) as raised:
        await read_fleet(hmc, _reader({E2_UUID: error}), "LPARs")
    assert raised.value is error
