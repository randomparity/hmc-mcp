"""System identity resolution reads ``ManagedSystem/quick/All`` (#1501).

The 2026-10-13 capture (#1290) saw the full ``ManagedSystem`` feed, the
``SystemName`` search for a ``No Connection`` system, and that system's
``ManagedSystem/{uuid}`` entry time out. ``quick/All`` answered; its captured
``rest-ms-quick-all-no-connection-v11r2`` record lists eleven systems, two of
them (``sys-R8``, ``sys-R11``) in ``no connection``. Every stalling route here
raises a read timeout, so a test fails if the code under test reaches one.
"""

from __future__ import annotations

import json
import re
from unittest.mock import patch

import httpx
import pytest
from conftest import captured_lpar_entry, live_fixture, live_response, make_config

from hmcpctl.client.core import HMCClient
from hmcpctl.resource_identity import (
    ResourceNotFoundError,
    resolve_system_uuid,
    system_name_from_uuid,
)
from hmcpctl.ssh.selectors import resolve_system_name

QUICK_ALL = "rest-ms-quick-all-no-connection-v11r2"
SUMMARIES = json.loads(live_fixture(QUICK_ALL)["body"])
UUIDS = {summary["SystemName"]: summary["UUID"] for summary in SUMMARIES}
DISCONNECTED = ("sys-R11", "sys-R8")
LPAR_A = "11111111-0000-4000-8000-00000000000a"
LPAR_B = "11111111-0000-4000-8000-00000000000b"

LOOKUPS = {
    "LPAR": ("find_partition_by_name", "LogicalPartition"),
    "VIOS": ("find_vios_by_name", "VirtualIOServer"),
}


def _feed(*entries: str) -> str:
    return '<feed xmlns="http://www.w3.org/2005/Atom">' + "".join(entries) + "</feed>"


def _stalling_routes(mock_hmc) -> list:
    """The reads that timed out on V11R2 SP1120 while a system had no connection."""
    stalled = httpx.ReadTimeout("read timed out")
    return [
        mock_hmc.get(url__regex=r"/rest/api/uom/ManagedSystem/?$").mock(
            side_effect=stalled
        ),
        mock_hmc.get(url__regex=r"/rest/api/uom/ManagedSystem/search/.*").mock(
            side_effect=stalled
        ),
        mock_hmc.get(url__regex=r"/rest/api/uom/ManagedSystem/[0-9a-f-]{36}$").mock(
            side_effect=stalled
        ),
    ]


def _quick_all(mock_hmc):
    path, response = live_response(QUICK_ALL)
    return mock_hmc.get(path).mock(return_value=response)


def _assert_unread(stalling) -> None:
    assert [route.call_count for route in stalling] == [0, 0, 0]


async def _resolve(value: str) -> str:
    async with HMCClient(make_config()) as hmc:
        return await resolve_system_uuid(hmc, value)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["sys-R8", "sys-R1"])
async def test_a_system_name_resolves_from_quick_all(mock_hmc, name):
    stalling = _stalling_routes(mock_hmc)
    quick_all = _quick_all(mock_hmc)

    assert await _resolve(name) == UUIDS[name]

    assert quick_all.call_count == 1
    _assert_unread(stalling)


@pytest.mark.asyncio
async def test_a_name_quick_all_does_not_list_is_not_found(mock_hmc):
    stalling = _stalling_routes(mock_hmc)
    _quick_all(mock_hmc)

    with pytest.raises(
        ResourceNotFoundError, match="No managed system named 'sys-R99'"
    ):
        await _resolve("sys-R99")

    _assert_unread(stalling)


@pytest.mark.asyncio
async def test_a_name_matches_only_in_its_exact_case(mock_hmc):
    _quick_all(mock_hmc)

    with pytest.raises(ResourceNotFoundError, match="'SYS-R1'"):
        await _resolve("SYS-R1")


@pytest.mark.asyncio
async def test_a_repeated_summary_is_one_match(mock_hmc):
    mock_hmc.get("/rest/api/uom/ManagedSystem/quick/All").mock(
        return_value=httpx.Response(200, json=[*SUMMARIES, SUMMARIES[0]])
    )

    assert await _resolve("sys-R1") == UUIDS["sys-R1"]


@pytest.mark.asyncio
async def test_a_name_two_summaries_share_is_ambiguous(mock_hmc):
    duplicate = {**SUMMARIES[0], "UUID": UUIDS["sys-R2"]}
    mock_hmc.get("/rest/api/uom/ManagedSystem/quick/All").mock(
        return_value=httpx.Response(200, json=[*SUMMARIES, duplicate])
    )

    with pytest.raises(ValueError) as raised:
        await _resolve("sys-R1")

    message = str(raised.value)
    assert message.startswith("Ambiguous managed-system name 'sys-R1': ")
    assert UUIDS["sys-R1"] in message and UUIDS["sys-R2"] in message


@pytest.mark.asyncio
async def test_a_matching_summary_without_a_uuid_is_refused(mock_hmc):
    mock_hmc.get("/rest/api/uom/ManagedSystem/quick/All").mock(
        return_value=httpx.Response(200, json=[{"SystemName": "sys-R1"}])
    )

    with pytest.raises(ValueError, match="UUID metadata is incomplete"):
        await _resolve("sys-R1")


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["sys-R8", "sys-R1"])
async def test_a_system_uuid_names_its_system_from_quick_all(mock_hmc, name):
    stalling = _stalling_routes(mock_hmc)
    _quick_all(mock_hmc)

    async with HMCClient(make_config()) as hmc:
        assert await system_name_from_uuid(hmc, UUIDS[name].upper()) == name

    _assert_unread(stalling)


@pytest.mark.asyncio
async def test_a_uuid_quick_all_does_not_list_has_no_name(mock_hmc):
    stalling = _stalling_routes(mock_hmc)
    _quick_all(mock_hmc)

    async with HMCClient(make_config()) as hmc:
        assert await system_name_from_uuid(hmc, LPAR_A) is None

    _assert_unread(stalling)


@pytest.mark.asyncio
async def test_an_ssh_selector_names_a_disconnected_system_without_ssh(mock_hmc):
    stalling = _stalling_routes(mock_hmc)
    _quick_all(mock_hmc)

    with patch("hmcpctl.ssh.transport.asyncssh.connect") as connect:
        assert await resolve_system_name(make_config(), UUIDS["sys-R11"]) == "sys-R11"

    connect.assert_not_called()
    _assert_unread(stalling)


def _system_feeds(mock_hmc, collection: str, owners: dict[str, str]):
    """Each system's scoped feed: the candidate it owns, or an empty feed.

    A ``No Connection`` system answers 204, as captured in #1289.
    """
    pattern = re.compile(rf"/ManagedSystem/([0-9a-f-]{{36}})/{collection}$")

    def answer(request: httpx.Request) -> httpx.Response:
        system_uuid = pattern.search(request.url.path).group(1)
        if system_uuid in {UUIDS[name] for name in DISCONNECTED}:
            return httpx.Response(204)
        owned = [lpar for lpar, owner in owners.items() if UUIDS[owner] == system_uuid]
        entries = [captured_lpar_entry(lpar, "shared") for lpar in owned]
        return httpx.Response(200, text=_feed(*entries))

    return mock_hmc.get(url__regex=pattern.pattern).mock(side_effect=answer)


def _search(mock_hmc, collection: str):
    return mock_hmc.get(
        f"/rest/api/uom/{collection}/search/(PartitionName==shared)"
    ).mock(
        return_value=httpx.Response(
            200,
            text=_feed(
                captured_lpar_entry(LPAR_A, "shared"),
                captured_lpar_entry(LPAR_B, "shared"),
            ),
        )
    )


async def _find(label: str, system_uuid: str | None = None):
    method, _ = LOOKUPS[label]
    async with HMCClient(make_config()) as hmc:
        if system_uuid is None:
            return await getattr(hmc, method)("shared")
        return await getattr(hmc, method)("shared", system_uuid=system_uuid)


@pytest.mark.asyncio
@pytest.mark.parametrize("label", sorted(LOOKUPS))
async def test_an_ambiguous_name_names_its_parents_from_quick_all(mock_hmc, label):
    _, collection = LOOKUPS[label]
    stalling = _stalling_routes(mock_hmc)
    _quick_all(mock_hmc)
    _search(mock_hmc, collection)
    feeds = _system_feeds(mock_hmc, collection, {LPAR_A: "sys-R3", LPAR_B: "sys-R1"})

    with pytest.raises(ValueError) as raised:
        await _find(label)

    assert str(raised.value) == (
        f"Ambiguous {label} name 'shared': "
        f"{LPAR_B} on 'sys-R1' ({UUIDS['sys-R1']}), "
        f"{LPAR_A} on 'sys-R3' ({UUIDS['sys-R3']})"
    )
    read = {call.request.url.path.split("/")[5] for call in feeds.calls}
    assert read == {UUIDS[name] for name in UUIDS if name not in DISCONNECTED}
    _assert_unread(stalling)


@pytest.mark.asyncio
@pytest.mark.parametrize("label", sorted(LOOKUPS))
async def test_an_unplaced_candidate_names_the_unreadable_systems(mock_hmc, label):
    _, collection = LOOKUPS[label]
    stalling = _stalling_routes(mock_hmc)
    _quick_all(mock_hmc)
    _search(mock_hmc, collection)
    _system_feeds(mock_hmc, collection, {LPAR_A: "sys-R3"})

    with pytest.raises(ValueError) as raised:
        await _find(label)

    message = str(raised.value)
    assert f"candidates {LPAR_B} must each belong to exactly one" in message
    for name in DISCONNECTED:
        assert f"'{name}' ({UUIDS[name]}) in State 'no connection'" in message
    assert "not operating, so were not read" in message
    _assert_unread(stalling)


@pytest.mark.asyncio
@pytest.mark.parametrize("label", sorted(LOOKUPS))
async def test_a_scoped_ambiguity_names_its_system_from_quick_all(mock_hmc, label):
    _, collection = LOOKUPS[label]
    stalling = _stalling_routes(mock_hmc)
    _quick_all(mock_hmc)
    _system_feeds(mock_hmc, collection, {LPAR_A: "sys-R3", LPAR_B: "sys-R3"})

    with pytest.raises(ValueError) as raised:
        await _find(label, UUIDS["sys-R3"])

    assert str(raised.value) == (
        f"Ambiguous {label} name 'shared': "
        f"{LPAR_A} on 'sys-R3' ({UUIDS['sys-R3']}), "
        f"{LPAR_B} on 'sys-R3' ({UUIDS['sys-R3']})"
    )
    _assert_unread(stalling)
