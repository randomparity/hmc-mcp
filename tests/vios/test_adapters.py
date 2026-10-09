"""Tests for the virtual adapter document builders."""

import xml.etree.ElementTree as ET

import pytest
from conftest import live_fixture

from hmcpctl.documents import (
    build_client_network_adapter_document,
    build_vfc_adapter_document,
    build_vscsi_adapter_document,
)
from hmcpctl.documents.common import UOM_NS


def test_vscsi_adapter():
    xml = build_vscsi_adapter_document(vios_partition_id=1, vios_slot=5, slot_number=10)
    assert "VirtualSCSIClientAdapter" in xml
    assert "<AdapterType" in xml and "Client" in xml
    assert "<RemoteLogicalPartitionID" in xml and ">1<" in xml
    assert "<RemoteSlotNumber" in xml and ">5<" in xml
    assert "<VirtualSlotNumber" in xml and ">10<" in xml


def test_vscsi_adapter_auto_slot():
    xml = build_vscsi_adapter_document(vios_partition_id=2, vios_slot=7)
    assert "VirtualSlotNumber" not in xml
    assert ">2<" in xml and ">7<" in xml


def test_vfc_adapter():
    xml = build_vfc_adapter_document(vios_partition_id=1, vios_slot=6, slot_number=11)
    assert "VirtualFibreChannelClientAdapter" in xml
    assert "<ConnectingPartitionID" in xml and ">1<" in xml
    assert "<ConnectingVirtualSlotNumber" in xml and ">6<" in xml
    assert "<VirtualSlotNumber" in xml and ">11<" in xml


def test_network_adapter_minimal():
    xml = build_client_network_adapter_document(port_vlan_id=100)
    assert "ClientNetworkAdapter" in xml
    assert "<PortVLANID" in xml and ">100<" in xml
    assert "TaggedVLANSupported" not in xml
    assert "MACAddress" not in xml
    assert "VirtualSwitchID" not in xml


def test_network_adapter_full():
    xml = build_client_network_adapter_document(
        port_vlan_id=200,
        slot_number=9,
        virtual_switch_id=3,
        tagged=True,
        mac_address="020000000001",
    )
    assert ">200<" in xml
    assert "<VirtualSlotNumber" in xml and ">9<" in xml
    assert "<VirtualSwitchID" in xml and ">3<" in xml
    assert "TaggedVLANSupported" in xml and "true" in xml
    assert "020000000001" in xml


def _children(xml: str, tag: str) -> list[ET.Element]:
    root = ET.fromstring(xml.encode())
    element = next(root.iter(f"{{{UOM_NS}}}{tag}"))
    return [child for child in element if child.tag != f"{{{UOM_NS}}}Metadata"]


def test_network_adapter_document_follows_the_served_virtual_ethernet_schema():
    """Order, names, kb and ksv match a served VirtualEthernetAdapter (#1399).

    V10R3 refused a create that put VirtualSwitchID before PortVLANID with
    REST0001: "Invalid content was found starting with element PortVLANID. One of
    VirtualSwitchName, VirtualStationInterfaceTypeVersion, DeviceName, ... is
    expected." No ClientNetworkAdapter instance is captured, so the evidence is the
    VIOS TrunkAdapter, which shares the VirtualEthernetAdapter base. The tagged
    flag is TaggedVLANSupported there and in every vocabulary capture; no capture
    carries IsTaggedVLAN.
    """
    served = _children(live_fixture("rest-vios-entry")["body"], "TrunkAdapter")
    sent = _children(
        build_client_network_adapter_document(
            port_vlan_id=200,
            slot_number=9,
            virtual_switch_id=3,
            tagged=True,
            mac_address="020000000001",
        ),
        "ClientNetworkAdapter",
    )
    served_by_tag = {el.tag: el for el in served}
    sent_tags = [el.tag for el in sent]

    assert set(sent_tags) <= set(served_by_tag)
    assert sent_tags == [el.tag for el in served if el.tag in sent_tags]
    assert [(el.get("kb"), el.get("ksv")) for el in sent] == [
        (served_by_tag[tag].get("kb"), served_by_tag[tag].get("ksv"))
        for tag in sent_tags
    ]


# chhwres documents a virtual Ethernet `mac_addr` as "12 hexadecimal
# characters" (docs/refs/hmc-commands-p10/commands/chhwres.md:238), and the
# captured vNIC readback and profile listing print MACs in that form (#1202).
@pytest.mark.parametrize(
    "mac", ["02:00:00:00:00:01", "02-00-00-00-00-01", "02000000001", "02000000000G"]
)
def test_network_adapter_rejects_mac_not_twelve_hex_digits(mac):
    with pytest.raises(ValueError, match="12 hexadecimal digits"):
        build_client_network_adapter_document(port_vlan_id=1, mac_address=mac)
