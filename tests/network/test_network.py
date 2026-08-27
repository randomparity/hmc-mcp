"""Tests for Virtual Network management (templates + client)."""

from unittest.mock import ANY, AsyncMock, patch

import httpx
import pytest
from conftest import make_config

from hmc_mcp.client import HMCClient
from hmc_mcp.documents import build_virtual_network_document
from hmc_mcp.server_tools.network import (
    hmc_create_virtual_network as hmc_create_virtual_network,
)
from hmc_mcp.server_tools.network import (
    hmc_delete_virtual_network as hmc_delete_virtual_network,
)
from hmc_mcp.server_tools.network import (
    hmc_list_network_bridges as hmc_list_network_bridges,
)
from hmc_mcp.server_tools.network import (
    hmc_list_virtual_networks as hmc_list_virtual_networks,
)
from hmc_mcp.server_tools.network import (
    hmc_list_virtual_switches as hmc_list_virtual_switches,
)

VSWITCH_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:vswitch-uuid-1</id>
    <title>VirtualSwitch:ETHERNET0</title>
    <link rel="SELF" href="https://hmc.test:12443/rest/api/uom/ManagedSystem/sys-uuid/VirtualSwitch/vswitch-uuid-1"/>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <VirtualSwitch xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <SwitchName>ETHERNET0</SwitchName>
        <SwitchID>3</SwitchID>
        <SwitchMode>VEB</SwitchMode>
      </VirtualSwitch>
    </content>
  </entry>
</feed>
"""

VNETWORK_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:vnet-uuid-1</id>
    <title>VirtualNetwork:VLAN100-ETHERNET0</title>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <VirtualNetwork xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <NetworkName>VLAN100-ETHERNET0</NetworkName>
        <NetworkVLANID>100</NetworkVLANID>
        <VswitchID>3</VswitchID>
        <TaggedNetwork>false</TaggedNetwork>
      </VirtualNetwork>
    </content>
  </entry>
</feed>
"""

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
    resolver = AsyncMock(return_value="sys-uuid")
    with patch("hmc_mcp.operations.network.resolve_system_uuid", new=resolver):
        result = tool("system-name", *args, **kwargs)
    resolver.assert_awaited_once_with(ANY, "system-name")
    return result


def test_virtual_network_document():
    xml = build_virtual_network_document(
        "VLAN100-ETHERNET0", 100, 3,
        switch_link="https://hmc.test:12443/rest/api/uom/ManagedSystem/sys-uuid/VirtualSwitch/vswitch-uuid-1",
    )
    assert "VirtualNetwork" in xml
    assert "<NetworkName" in xml and "VLAN100-ETHERNET0" in xml
    assert "<NetworkVLANID" in xml and ">100<" in xml
    assert "<VswitchID" in xml and ">3<" in xml
    assert "AssociatedSwitch" in xml and "VirtualSwitch/vswitch-uuid-1" in xml


def test_virtual_network_document_tagged():
    xml = build_virtual_network_document("n", 200, 3, tagged=True)
    assert "TaggedNetwork" in xml and ">true<" in xml


@pytest.mark.asyncio
async def test_list_virtual_switches(mock_hmc):
    mock_hmc.get("/rest/api/uom/ManagedSystem/sys-uuid/VirtualSwitch").mock(
        return_value=httpx.Response(200, text=VSWITCH_FEED)
    )
    async with HMCClient(make_config()) as hmc:
        switches = await hmc.list_virtual_switches("sys-uuid")
    assert len(switches) == 1
    assert switches[0]["Resource"]["SwitchName"] == "ETHERNET0"
    assert switches[0]["Resource"]["SwitchID"] == "3"


@pytest.mark.asyncio
async def test_list_virtual_networks(mock_hmc):
    mock_hmc.get("/rest/api/uom/ManagedSystem/sys-uuid/VirtualNetwork").mock(
        return_value=httpx.Response(200, text=VNETWORK_FEED)
    )
    async with HMCClient(make_config()) as hmc:
        nets = await hmc.list_virtual_networks("sys-uuid")
    assert len(nets) == 1
    assert nets[0]["Resource"]["NetworkVLANID"] == "100"


@pytest.mark.asyncio
async def test_create_virtual_network(mock_hmc):
    route = mock_hmc.put("/rest/api/uom/ManagedSystem/sys-uuid/VirtualNetwork").mock(
        return_value=httpx.Response(201, text=VNETWORK_ENTRY)
    )
    async with HMCClient(make_config()) as hmc:
        net = await hmc.create_virtual_network(
            "sys-uuid", "VLAN100-ETHERNET0", 100, 3, switch_uuid="vswitch-uuid-1"
        )
    body = route.calls.last.request.content.decode()
    assert "VLAN100-ETHERNET0" in body and ">100<" in body and ">3<" in body
    assert "VirtualSwitch/vswitch-uuid-1" in body
    assert net is not None


@pytest.mark.asyncio
async def test_delete_virtual_network(mock_hmc):
    route = mock_hmc.delete(
        "/rest/api/uom/ManagedSystem/sys-uuid/VirtualNetwork/vnet-uuid-1"
    ).mock(return_value=httpx.Response(204))
    async with HMCClient(make_config()) as hmc:
        await hmc.delete_virtual_network("sys-uuid", "vnet-uuid-1")
    assert route.called


@pytest.mark.asyncio
async def test_list_network_bridges(mock_hmc):
    mock_hmc.get("/rest/api/uom/ManagedSystem/sys-uuid/NetworkBridge").mock(
        return_value=httpx.Response(200, text=NETWORKBRIDGE_FEED)
    )
    async with HMCClient(make_config()) as hmc:
        bridges = await hmc.list_network_bridges("sys-uuid")
    assert len(bridges) == 1
    assert bridges[0]["ResourceType"] == "NetworkBridge"


@pytest.mark.parametrize(
    ("tool", "suffix", "feed", "resource_type"),
    [
        (hmc_list_virtual_switches, "VirtualSwitch", VSWITCH_FEED, "VirtualSwitch"),
        (hmc_list_virtual_networks, "VirtualNetwork", VNETWORK_FEED, "VirtualNetwork"),
        (hmc_list_network_bridges, "NetworkBridge", NETWORKBRIDGE_FEED, "NetworkBridge"),
    ],
)
def test_network_list_tools_resolve_public_system_selector(
    monkeypatch, mock_hmc, tool, suffix, feed, resource_type
):
    route = mock_hmc.get(f"/rest/api/uom/ManagedSystem/sys-uuid/{suffix}").mock(
        return_value=httpx.Response(200, text=feed)
    )

    result = _call_tool_with_resolved_system(monkeypatch, tool)

    assert route.called
    assert result[0]["ResourceType"] == resource_type


def test_create_virtual_network_tool_maps_public_arguments(monkeypatch, mock_hmc):
    route = mock_hmc.put("/rest/api/uom/ManagedSystem/sys-uuid/VirtualNetwork").mock(
        return_value=httpx.Response(201, text=VNETWORK_ENTRY)
    )

    result = _call_tool_with_resolved_system(
        monkeypatch,
        hmc_create_virtual_network,
        "VLAN100-ETHERNET0",
        100,
        3,
        tagged=True,
    )

    body = route.calls.last.request.content.decode()
    assert result["UUID"] == "vnet-uuid-1"
    assert "VLAN100-ETHERNET0" in body
    assert ">100<" in body and ">3<" in body and ">true<" in body


def test_delete_virtual_network_tool_maps_public_arguments(monkeypatch, mock_hmc):
    route = mock_hmc.delete(
        "/rest/api/uom/ManagedSystem/sys-uuid/VirtualNetwork/vnet-uuid-1"
    ).mock(return_value=httpx.Response(204))

    result = _call_tool_with_resolved_system(
        monkeypatch, hmc_delete_virtual_network, "vnet-uuid-1"
    )

    assert route.called
    assert result == "Deleted VirtualNetwork vnet-uuid-1 from system-name"
