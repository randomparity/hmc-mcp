"""An empty per-system feed from a system that is not operating (#1289).

V11R2 SP1120 answered ``ManagedSystem/{uuid}/LogicalPartition`` and
``.../VirtualIOServer`` with 204 and no body while a POWER10 9080-HEX read
``State`` ``recovery`` (``DetailedState`` ``Recovery``) and later
``no connection`` (``Unknown``). The values are in
``tests/fixtures/live/vocabulary/v11r2-p10-9080-hex.json``.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from conftest import captured_lpar_entry, make_config

from hmcpctl.client.core import HMCClient
from hmcpctl.errors import HMCError
from hmcpctl.operations.lpar.core import get_lpar, list_lpars
from hmcpctl.operations.systems.health import fetch_fleet_health
from hmcpctl.operations.vios.core import list_vios
from hmcpctl.resource_identity import (
    ResourceNotFoundError,
    resolve_lpar_uuid,
    resolve_vios_uuid,
)

SYSTEM_UUID = "11111111-1111-1111-1111-111111111111"
SYSTEM_NAME = "sys-R1"
SYSTEM_PATH = f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}"
LPAR_FEED = f"{SYSTEM_PATH}/LogicalPartition"
VIOS_FEED = f"{SYSTEM_PATH}/VirtualIOServer"

_VOCABULARY = json.loads(
    (
        Path(__file__).parents[1] / "fixtures/live/vocabulary/v11r2-p10-9080-hex.json"
    ).read_text()
)["rest"]["values"]

NOT_OPERATING = [("recovery", "Recovery"), ("no connection", "Unknown")]

Read = Callable[[HMCClient], Awaitable[Any]]
READS: dict[str, tuple[str, Read]] = {
    "client_list_lpars": (
        LPAR_FEED,
        lambda hmc: hmc.list_logical_partitions(SYSTEM_UUID),
    ),
    "client_list_vios": (VIOS_FEED, lambda hmc: hmc.list_vios(SYSTEM_UUID)),
    "list_lpars": (LPAR_FEED, lambda hmc: list_lpars(hmc, SYSTEM_UUID)),
    "get_lpar": (
        LPAR_FEED,
        lambda hmc: get_lpar(hmc, "lpar-a", system_name_or_uuid=SYSTEM_UUID),
    ),
    "resolve_lpar_uuid": (
        LPAR_FEED,
        lambda hmc: resolve_lpar_uuid(hmc, "lpar-a", system_name_or_uuid=SYSTEM_UUID),
    ),
    "list_vios": (VIOS_FEED, lambda hmc: list_vios(hmc, SYSTEM_UUID)),
    "resolve_vios_uuid": (
        VIOS_FEED,
        lambda hmc: resolve_vios_uuid(hmc, "vios-a", system_name_or_uuid=SYSTEM_UUID),
    ),
}


def _system(*elements: str) -> str:
    body = "".join(elements)
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:{SYSTEM_UUID}</id>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <ManagedSystem xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <SystemName>{SYSTEM_NAME}</SystemName>{body}
    </ManagedSystem>
  </content>
</entry>
"""


def _state(state: str, detailed: str) -> str:
    return _system(
        f"<State>{state}</State>", f"<DetailedState>{detailed}</DetailedState>"
    )


def test_states_are_the_captured_values() -> None:
    for state, detailed in [*NOT_OPERATING, ("operating", "None")]:
        assert state in _VOCABULARY["State"]
        assert detailed in _VOCABULARY["DetailedState"]


@pytest.mark.asyncio
@pytest.mark.parametrize("read", sorted(READS))
@pytest.mark.parametrize(("state", "detailed"), NOT_OPERATING)
async def test_empty_feed_from_a_system_not_operating_raises(
    mock_hmc, read, state, detailed
) -> None:
    feed, call = READS[read]
    mock_hmc.get(feed).mock(return_value=httpx.Response(204))
    mock_hmc.get(SYSTEM_PATH).mock(
        return_value=httpx.Response(200, text=_state(state, detailed))
    )

    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError) as caught:
            await call(hmc)

    message = str(caught.value)
    assert repr(SYSTEM_NAME) in message
    assert SYSTEM_UUID in message
    assert f"State {state!r}" in message
    assert f"DetailedState {detailed!r}" in message
    assert caught.value.status_code is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "read", ["list_lpars", "list_vios", "client_list_lpars", "client_list_vios"]
)
@pytest.mark.parametrize("state", ["operating", "Operating"])
async def test_empty_feed_from_an_operating_system_is_empty(
    mock_hmc, read, state
) -> None:
    feed, call = READS[read]
    mock_hmc.get(feed).mock(return_value=httpx.Response(204))
    system = mock_hmc.get(SYSTEM_PATH).mock(
        return_value=httpx.Response(200, text=_state(state, "None"))
    )

    async with HMCClient(make_config()) as hmc:
        assert await call(hmc) == []

    assert system.call_count == 1


@pytest.mark.asyncio
async def test_scoped_lpar_lookup_on_an_operating_system_is_not_found(mock_hmc):
    mock_hmc.get(LPAR_FEED).mock(return_value=httpx.Response(204))
    system = mock_hmc.get(SYSTEM_PATH).mock(
        return_value=httpx.Response(200, text=_state("operating", "None"))
    )

    async with HMCClient(make_config()) as hmc:
        assert await get_lpar(hmc, "lpar-a", system_name_or_uuid=SYSTEM_UUID) is None

    assert system.call_count == 1


@pytest.mark.asyncio
async def test_scoped_vios_lookup_on_an_operating_system_is_not_found(mock_hmc):
    mock_hmc.get(VIOS_FEED).mock(return_value=httpx.Response(204))
    system = mock_hmc.get(SYSTEM_PATH).mock(
        return_value=httpx.Response(200, text=_state("operating", "None"))
    )

    async with HMCClient(make_config()) as hmc:
        with pytest.raises(ResourceNotFoundError):
            await resolve_vios_uuid(hmc, "vios-a", system_name_or_uuid=SYSTEM_UUID)

    assert system.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "document", [_system(), None], ids=["no-state-elements", "no-document"]
)
async def test_unreadable_state_reads_unknown(mock_hmc, document) -> None:
    mock_hmc.get(LPAR_FEED).mock(return_value=httpx.Response(204))
    mock_hmc.get(SYSTEM_PATH).mock(
        return_value=httpx.Response(200, text=document)
        if document is not None
        else httpx.Response(204)
    )

    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError) as caught:
            await list_lpars(hmc, SYSTEM_UUID)

    message = str(caught.value)
    assert "State 'unknown'" in message
    assert "DetailedState 'unknown'" in message


@pytest.mark.asyncio
async def test_non_empty_feed_does_not_read_the_system(mock_hmc) -> None:
    lpar_uuid = "22222222-2222-2222-2222-222222222222"
    feed = (
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        f"{captured_lpar_entry(lpar_uuid, 'lpar-a')}</feed>"
    )
    mock_hmc.get(LPAR_FEED).mock(return_value=httpx.Response(200, text=feed))
    system = mock_hmc.get(SYSTEM_PATH).mock(
        return_value=httpx.Response(200, text=_state("recovery", "Recovery"))
    )

    async with HMCClient(make_config()) as hmc:
        assert await list_lpars(hmc, SYSTEM_UUID, state="running") == []
        assert await get_lpar(hmc, "lpar-b", system_name_or_uuid=SYSTEM_UUID) is None

    assert not system.called


@pytest.mark.asyncio
async def test_non_empty_vios_feed_does_not_read_the_system(mock_hmc) -> None:
    vios_uuid = "22222222-2222-2222-2222-222222222222"
    entry = captured_lpar_entry(vios_uuid, "vios-a", "running").replace(
        "LogicalPartition", "VirtualIOServer"
    )
    feed = f'<feed xmlns="http://www.w3.org/2005/Atom">{entry}</feed>'
    mock_hmc.get(VIOS_FEED).mock(return_value=httpx.Response(200, text=feed))
    system = mock_hmc.get(SYSTEM_PATH).mock(
        return_value=httpx.Response(200, text=_state("recovery", "Recovery"))
    )

    async with HMCClient(make_config()) as hmc:
        (vios,) = await hmc.list_vios(SYSTEM_UUID)

    assert vios["UUID"] == vios_uuid
    assert not system.called


def _fleet_entry(state: str, uuid: str = SYSTEM_UUID, name: str = SYSTEM_NAME):
    return {"UUID": uuid, "Resource": {"SystemName": name, "State": state}}


def _mock_no_connection_system(router) -> None:
    router.get(LPAR_FEED).mock(return_value=httpx.Response(204))
    router.get(VIOS_FEED).mock(return_value=httpx.Response(204))
    router.get(SYSTEM_PATH).mock(
        return_value=httpx.Response(200, text=_state("no connection", "Unknown"))
    )


@pytest.mark.asyncio
async def test_fleet_health_warns_for_a_non_operating_system(
    mock_hmc, monkeypatch
) -> None:
    _mock_no_connection_system(mock_hmc)

    async with HMCClient(make_config()) as hmc:
        fleet = AsyncMock(return_value=[_fleet_entry("no connection")])
        monkeypatch.setattr(hmc, "list_managed_systems", fleet)
        result = await fetch_fleet_health(hmc)

    assert [system["state"] for system in result.systems] == ["no connection"]
    assert result.lpars == () and result.vios == ()
    lpar_warning, vios_warning = result.warnings
    assert lpar_warning.startswith(f"LPAR inventory for system {SYSTEM_NAME}")
    assert vios_warning.startswith(f"VIOS inventory for system {SYSTEM_NAME}")
    assert all("State 'no connection'" in warning for warning in result.warnings)
