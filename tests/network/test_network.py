"""Tests for Virtual Network management (templates + client)."""

import xml.etree.ElementTree as ET
from unittest.mock import ANY, AsyncMock, patch

import httpx
import pytest
from conftest import live_fixture, make_config

from hmcpctl.client.core import HMCClient
from hmcpctl.documents import build_virtual_network_document
from hmcpctl.operations.virtualization.network import (
    VirtualNetworkResult,
    create_virtual_network,
)
from hmcpctl.resource_identity import ResourceNotFoundError
from hmcpctl.server_tools.virtualization.network import (
    hmc_create_virtual_network,
    hmc_delete_virtual_network,
    hmc_list_network_bridges,
    hmc_list_virtual_networks,
    hmc_list_virtual_switches,
)
from hmcpctl.xmlutil import parse_feed

SYSTEM_UUID = "00000000-0000-0000-0000-000000000001"
VNETWORK_UUID = "00000000-0000-0000-0000-000000000002"

# The V10R3 feeds for one POWER9 system: two Veb-mode switches, and two
# untagged networks on switch 0 (#1202).
VSWITCH_FEED = live_fixture("rest-virtual-switch-feed")["body"]
VNETWORK_FEED = live_fixture("rest-virtual-network-feed")["body"]
SWITCH_1_UUID = "0000000b-abcd-4ef0-8abc-00000000000b"
UOM = "{http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/}"

VNETWORK_ENTRY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:vnet-uuid-1</id>
  <title>VirtualNetwork:VLAN100-ETHERNET0</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <VirtualNetwork xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <NetworkName>VLAN100-ETHERNET0</NetworkName>
      <NetworkVLANID>100</NetworkVLANID>
    </VirtualNetwork>
  </content>
</entry>
"""

NETWORKBRIDGE_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:nb-uuid-1</id>
    <title>NetworkBridge:bridge1</title>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <NetworkBridge xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <PortVLANID>1</PortVLANID>
      </NetworkBridge>
    </content>
  </entry>
</feed>
"""


def _hmc_env(monkeypatch):
    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "secret")


def _call_tool_with_resolved_system(monkeypatch, tool, *args, **kwargs):
    _hmc_env(monkeypatch)
    resolver = AsyncMock(return_value=SYSTEM_UUID)
    with patch(
        "hmcpctl.operations.virtualization.network.resolve_system_uuid", new=resolver
    ):
        result = tool("system-name", *args, **kwargs)
    resolver.assert_awaited_once_with(ANY, "system-name")
    return result


@pytest.mark.asyncio
async def test_create_virtual_network_operation_returns_parent_and_resource(
    monkeypatch,
):
    resource = {"UUID": "vnet-uuid-1"}
    hmc = _switch_hmc()
    hmc.create_virtual_network.return_value = resource
    resolver = AsyncMock(return_value="sys-uuid")
    monkeypatch.setattr(
        "hmcpctl.operations.virtualization.network.resolve_system_uuid", resolver
    )

    result = await create_virtual_network(hmc, "system-name", "prod", 100, 1)

    assert result == VirtualNetworkResult("sys-uuid", resource)
    hmc.list_virtual_switches.assert_awaited_once_with("sys-uuid")
    hmc.create_virtual_network.assert_awaited_once_with(
        "sys-uuid", "prod", 100, 1, switch_uuid=SWITCH_1_UUID, tagged=False
    )


@pytest.mark.asyncio
async def test_create_virtual_network_refuses_an_unknown_switch_id_before_the_put():
    """A SwitchID no VirtualSwitch carries has no AssociatedSwitch link to send (#1374)."""
    hmc = _switch_hmc()
    with (
        patch(
            "hmcpctl.operations.virtualization.network.resolve_system_uuid",
            AsyncMock(return_value=SYSTEM_UUID),
        ),
        pytest.raises(ResourceNotFoundError, match=r"SwitchID 3 .*0, 1") as caught,
    ):
        await create_virtual_network(hmc, "system-name", "net", 100, 3)

    assert (caught.value.resource_kind, caught.value.selector) == ("VirtualSwitch", "3")
    hmc.create_virtual_network.assert_not_awaited()


def test_virtual_network_document():
    xml = build_virtual_network_document(
        "VLAN100-ETHERNET0",
        100,
        3,
        switch_link="https://hmc.test:12443/rest/api/uom/ManagedSystem/sys-uuid/VirtualSwitch/vswitch-uuid-1",
    )
    assert "VirtualNetwork" in xml
    assert "<NetworkName" in xml and "VLAN100-ETHERNET0" in xml
    assert "<NetworkVLANID" in xml and ">100<" in xml
    assert "<VswitchID" in xml and ">3<" in xml
    assert "AssociatedSwitch" in xml and "VirtualSwitch/vswitch-uuid-1" in xml


def _switch_hmc() -> AsyncMock:
    hmc = AsyncMock()
    hmc.list_virtual_switches.return_value = parse_feed(VSWITCH_FEED)
    return hmc


def _vnet_children(xml: str) -> list[ET.Element]:
    root = ET.fromstring(xml.encode())
    if root.tag != f"{UOM}VirtualNetwork":
        root = next(root.iter(f"{UOM}VirtualNetwork"))
    return list(root)


def test_virtual_network_document_places_associated_switch_as_the_live_feed_does():
    """V10R3 serves AssociatedSwitch in the UOM namespace right after Metadata (#1374).

    A create without it was refused with HTTP 500 "Associated VSwitch of
    VirtualNetwork cannot be null!!".
    """
    link = f"https://hmc.test:443/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/VirtualSwitch/{SWITCH_1_UUID}"
    sent = _vnet_children(build_virtual_network_document("n", 2, 1, link))
    served = _vnet_children(VNETWORK_FEED)
    served_order = [el.tag for el in served]

    assert [el.tag for el in sent] == [
        t for t in served_order if t in {e.tag for e in sent}
    ]
    switch = sent[1]
    assert switch.tag == f"{UOM}AssociatedSwitch"
    assert switch.attrib == {
        "kb": "COD",
        "kxe": "false",
        "href": link,
        "rel": "related",
    }


def test_virtual_network_document_kb_matches_the_live_feed():
    """Each element's kb is the one the V10R3 feed serves (2026-10-06 live run).

    A create carrying NetworkName kb="CUD" was refused with REST0001: "Value 'CUD'
    is not facet-valid with respect to enumeration '[CUR]'".
    """
    import re

    served = dict(re.findall(r"<(\w+) [^>]*?kb=\"(\w+)\"", VNETWORK_FEED))
    sent = dict(
        re.findall(
            r"<(\w+) kb=\"(\w+)\"",
            build_virtual_network_document("n", 2, 0, "https://hmc.test/switch"),
        )
    )

    assert "AssociatedSwitch" in sent
    assert {name: served[name] for name in sent} == sent


def test_virtual_network_document_tagged():
    xml = build_virtual_network_document("n", 200, 3, tagged=True)
    assert "TaggedNetwork" in xml and ">true<" in xml


@pytest.mark.asyncio
async def test_list_virtual_switches(mock_hmc):
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/VirtualSwitch").mock(
        return_value=httpx.Response(200, text=VSWITCH_FEED)
    )
    async with HMCClient(make_config()) as hmc:
        switches = await hmc.list_virtual_switches(SYSTEM_UUID)
    assert [
        (
            s["Resource"]["SwitchID"],
            s["Resource"]["SwitchName"],
            s["Resource"]["SwitchMode"],
        )
        for s in switches
    ] == [("0", "vswitch-1", "Veb"), ("1", "vswitch-2", "Veb")]


@pytest.mark.asyncio
async def test_list_virtual_networks(mock_hmc):
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/VirtualNetwork").mock(
        return_value=httpx.Response(200, text=VNETWORK_FEED)
    )
    async with HMCClient(make_config()) as hmc:
        nets = await hmc.list_virtual_networks(SYSTEM_UUID)
    assert [
        (
            n["Resource"]["NetworkName"],
            n["Resource"]["NetworkVLANID"],
            n["Resource"]["VswitchID"],
            n["Resource"]["TaggedNetwork"],
        )
        for n in nets
    ] == [("net-1", "1", "0", "false"), ("net-2", "2", "0", "false")]


@pytest.mark.asyncio
async def test_create_virtual_network(mock_hmc):
    route = mock_hmc.put(
        f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/VirtualNetwork"
    ).mock(return_value=httpx.Response(201, text=VNETWORK_ENTRY))
    async with HMCClient(make_config()) as hmc:
        net = await hmc.create_virtual_network(
            SYSTEM_UUID, "VLAN100-ETHERNET0", 100, 3, switch_uuid="vswitch-uuid-1"
        )
    body = route.calls.last.request.content.decode()
    assert "VLAN100-ETHERNET0" in body and ">100<" in body and ">3<" in body
    assert "VirtualSwitch/vswitch-uuid-1" in body
    assert net is not None


@pytest.mark.asyncio
async def test_delete_virtual_network(mock_hmc):
    route = mock_hmc.delete(
        f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/VirtualNetwork/{VNETWORK_UUID}"
    ).mock(return_value=httpx.Response(204))
    async with HMCClient(make_config()) as hmc:
        await hmc.delete_virtual_network(SYSTEM_UUID, VNETWORK_UUID)
    assert route.called


@pytest.mark.asyncio
async def test_list_network_bridges(mock_hmc):
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/NetworkBridge").mock(
        return_value=httpx.Response(200, text=NETWORKBRIDGE_FEED)
    )
    async with HMCClient(make_config()) as hmc:
        bridges = await hmc.list_network_bridges(SYSTEM_UUID)
    assert len(bridges) == 1
    assert bridges[0]["ResourceType"] == "NetworkBridge"


@pytest.mark.parametrize(
    ("tool", "suffix", "feed", "resource_type"),
    [
        (hmc_list_virtual_switches, "VirtualSwitch", VSWITCH_FEED, "VirtualSwitch"),
        (hmc_list_virtual_networks, "VirtualNetwork", VNETWORK_FEED, "VirtualNetwork"),
        (
            hmc_list_network_bridges,
            "NetworkBridge",
            NETWORKBRIDGE_FEED,
            "NetworkBridge",
        ),
    ],
)
def test_network_list_tools_resolve_public_system_selector(
    monkeypatch, mock_hmc, tool, suffix, feed, resource_type
):
    route = mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/{suffix}").mock(
        return_value=httpx.Response(200, text=feed)
    )

    result = _call_tool_with_resolved_system(monkeypatch, tool)

    assert route.called
    assert result[0]["ResourceType"] == resource_type


def test_create_virtual_network_tool_maps_public_arguments(monkeypatch, mock_hmc):
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/VirtualSwitch").mock(
        return_value=httpx.Response(200, text=VSWITCH_FEED)
    )
    route = mock_hmc.put(
        f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/VirtualNetwork"
    ).mock(return_value=httpx.Response(201, text=VNETWORK_ENTRY))

    result = _call_tool_with_resolved_system(
        monkeypatch,
        hmc_create_virtual_network,
        "VLAN100-ETHERNET0",
        100,
        1,
        tagged=True,
    )

    body = route.calls.last.request.content.decode()
    assert result["UUID"] == "vnet-uuid-1"
    assert "VLAN100-ETHERNET0" in body
    assert ">100<" in body and ">1<" in body and ">true<" in body
    switch = _vnet_children(body)[1]
    assert switch.tag == f"{UOM}AssociatedSwitch"
    assert switch.get("href") == (
        f"https://hmc.test/rest/api/uom/ManagedSystem/{SYSTEM_UUID}"
        f"/VirtualSwitch/{SWITCH_1_UUID}"
    )


def test_delete_virtual_network_tool_maps_public_arguments(monkeypatch, mock_hmc):
    route = mock_hmc.delete(
        f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/VirtualNetwork/{VNETWORK_UUID}"
    ).mock(return_value=httpx.Response(204))

    result = _call_tool_with_resolved_system(
        monkeypatch, hmc_delete_virtual_network, VNETWORK_UUID
    )

    assert route.called
    assert result == f"Deleted VirtualNetwork {VNETWORK_UUID} from system-name"


@pytest.mark.asyncio
@pytest.mark.parametrize("vlan_id", [0, 4095, 100000, -1, True])
async def test_create_virtual_network_refuses_out_of_range_vlan_before_any_read(
    vlan_id,
):
    """IEEE 802.1Q usable VLAN ids are 1-4094; anything else is refused before I/O."""
    hmc = AsyncMock()

    with pytest.raises(
        ValueError, match=r"vlan_id .* must be a VLAN id from 1 to 4094"
    ):
        await create_virtual_network(hmc, "system-name", "net", vlan_id, 0)

    assert hmc.mock_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("vlan_id", [1, 4094])
async def test_create_virtual_network_accepts_range_bounds(vlan_id):
    hmc = _switch_hmc()
    hmc.create_virtual_network.return_value = {"UUID": VNETWORK_UUID}
    with patch(
        "hmcpctl.operations.virtualization.network.resolve_system_uuid",
        AsyncMock(return_value=SYSTEM_UUID),
    ):
        await create_virtual_network(hmc, "system-name", "net", vlan_id, 0)

    hmc.create_virtual_network.assert_awaited_once_with(
        SYSTEM_UUID,
        "net",
        vlan_id,
        0,
        switch_uuid="0000000a-abcd-4ef0-8abc-00000000000a",
        tagged=False,
    )
