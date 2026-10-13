"""Unscoped name lookups and create-time name checks fail closed (#1309).

The fixtures mirror a 2026-10-13 read-only capture: the HMC-wide
``PartitionName`` searches answer 204 for a name on a ``No Connection`` system,
exactly as for a name that exists nowhere, while ``ManagedSystem/quick/All``
answers and reports that system's ``State`` as ``no connection``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from conftest import (
    assert_no_mutating_requests,
    captured_lpar_entry,
    make_config,
    mock_system_summaries,
)

from hmcpctl.client.core import HMCClient
from hmcpctl.documents import LparResources
from hmcpctl.errors import HMCError
from hmcpctl.operations.lpar.core import LparCreation, create_and_stamp_lpar
from hmcpctl.operations.lpar.provision import (
    ProvisionAdapters,
    ProvisionRequest,
    ProvisionStorage,
    provision_lpar,
)

SYS_A = "aaaaaaaa-0000-4000-8000-00000000000a"
SYS_B = "bbbbbbbb-0000-4000-8000-00000000000b"
LPAR_UUID = "11111111-0000-4000-8000-000000000001"
VIOS_UUID = "22222222-0000-4000-8000-000000000002"

SEARCHES = {
    "LPAR": ("find_partition_by_name", "LogicalPartition", "lpar-1"),
    "VIOS": ("find_vios_by_name", "VirtualIOServer", "vios-1"),
}

ONE_DISCONNECTED = ((SYS_A, "sys-A", "no connection"), (SYS_B, "sys-B", "operating"))
ALL_OPERATING = ((SYS_A, "sys-A", "operating"), (SYS_B, "sys-B", "operating"))


def _feed(*entries: str) -> str:
    return '<feed xmlns="http://www.w3.org/2005/Atom">' + "".join(entries) + "</feed>"


def _search(mock_hmc, collection: str, name: str, response: httpx.Response):
    return mock_hmc.get(
        f"/rest/api/uom/{collection}/search/(PartitionName=={name})"
    ).mock(return_value=response)


async def _lookup(label: str):
    method, _, name = SEARCHES[label]
    async with HMCClient(make_config()) as hmc:
        return await getattr(hmc, method)(name)


@pytest.mark.asyncio
@pytest.mark.parametrize("label", sorted(SEARCHES))
async def test_a_miss_while_a_system_has_no_connection_names_it(mock_hmc, label):
    _, collection, name = SEARCHES[label]
    _search(mock_hmc, collection, name, httpx.Response(204))
    mock_system_summaries(mock_hmc, *ONE_DISCONNECTED)

    with pytest.raises(HMCError) as raised:
        await _lookup(label)

    message = str(raised.value)
    assert f"No {label} named {name!r} was found on the readable managed" in message
    assert f"'sys-A' ({SYS_A}) in State 'no connection'" in message
    assert "sys-B" not in message
    assert "supply a managed-system selector" in message


@pytest.mark.asyncio
@pytest.mark.parametrize("label", sorted(SEARCHES))
async def test_a_miss_on_an_all_operating_fleet_is_not_found(mock_hmc, label):
    _, collection, name = SEARCHES[label]
    _search(mock_hmc, collection, name, httpx.Response(204))
    quick_all = mock_system_summaries(mock_hmc, *ALL_OPERATING)

    assert await _lookup(label) is None
    assert quick_all.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("label", sorted(SEARCHES))
async def test_a_hit_is_returned_without_reading_quick_all(mock_hmc, label):
    _, collection, name = SEARCHES[label]
    _search(
        mock_hmc,
        collection,
        name,
        httpx.Response(200, text=_feed(captured_lpar_entry(LPAR_UUID, name))),
    )
    quick_all = mock_system_summaries(mock_hmc, *ONE_DISCONNECTED)

    found = await _lookup(label)

    assert found["UUID"] == LPAR_UUID
    assert not quick_all.called


@pytest.mark.asyncio
async def test_a_summary_that_is_not_an_object_cannot_prove_absence(mock_hmc):
    _search(mock_hmc, "LogicalPartition", "lpar-1", httpx.Response(204))
    mock_hmc.get("/rest/api/uom/ManagedSystem/quick/All").mock(
        return_value=httpx.Response(200, json=["sys-A"])
    )

    with pytest.raises(HMCError, match="an unidentified managed system"):
        await _lookup("LPAR")


@pytest.mark.asyncio
async def test_a_failed_quick_all_read_fails_the_lookup(mock_hmc):
    _search(mock_hmc, "LogicalPartition", "lpar-1", httpx.Response(204))
    mock_hmc.get("/rest/api/uom/ManagedSystem/quick/All").mock(
        return_value=httpx.Response(500)
    )

    with pytest.raises(HMCError, match="quick/All failed"):
        await _lookup("LPAR")


def _disconnected_target(mock_hmc):
    """sys-A: its scoped feed answers 204 and its own entry says no connection."""
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYS_A}/LogicalPartition").mock(
        return_value=httpx.Response(204)
    )
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYS_A}").mock(
        return_value=httpx.Response(
            200,
            text='<entry xmlns="http://www.w3.org/2005/Atom">'
            f"<id>urn:uuid:{SYS_A}</id>"
            '<content type="application/vnd.ibm.powervm.uom+xml">'
            '<ManagedSystem xmlns="http://www.ibm.com/xmlns/systems/power/'
            'firmware/uom/mc/2012_10/">'
            "<SystemName>sys-A</SystemName><State>no connection</State>"
            "</ManagedSystem></content></entry>",
        )
    )
    return mock_hmc.get(url__regex=r"/rest/api/uom/LogicalPartition/search/.*").mock(
        return_value=httpx.Response(204)
    )


@pytest.mark.asyncio
async def test_create_refuses_before_any_write_when_the_target_cannot_be_read(
    mock_hmc,
):
    hmc_wide = _disconnected_target(mock_hmc)
    creation = LparCreation(
        name="lpar-1",
        partition_type="AIX/Linux",
        resources=LparResources(desired_memory=2048, desired_vcpus=1),
    )

    with (
        patch("hmcpctl.operations.lpar.core.create_lpar_via_cli") as via_cli,
        pytest.raises(HMCError, match="State 'no connection'"),
    ):
        async with HMCClient(make_config()) as hmc:
            await create_and_stamp_lpar(hmc, SYS_A, creation)

    via_cli.assert_not_called()
    assert not hmc_wide.called
    assert_no_mutating_requests(mock_hmc)


@pytest.mark.asyncio
async def test_provision_refuses_before_any_write_when_the_target_cannot_be_read(
    mock_hmc,
):
    hmc_wide = _disconnected_target(mock_hmc)
    request = ProvisionRequest(
        name="lpar-1",
        adapters=ProvisionAdapters(port_vlan_id=100),
        storage=ProvisionStorage(vios_uuid=VIOS_UUID, storage_name="lv_boot"),
        resources=LparResources(desired_memory=2048, desired_vcpus=1),
    )

    with (
        patch(
            "hmcpctl.operations.lpar.core.create_lpar_via_cli", new=AsyncMock()
        ) as via_cli,
        pytest.raises(HMCError, match="State 'no connection'"),
    ):
        async with HMCClient(make_config()) as hmc:
            await provision_lpar(hmc, SYS_A, request)

    via_cli.assert_not_awaited()
    assert not hmc_wide.called
    assert_no_mutating_requests(mock_hmc)
