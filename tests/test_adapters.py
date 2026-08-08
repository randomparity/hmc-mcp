"""Tests for the virtual adapter document builders."""

from hmc_mcp.templates import (
    build_client_network_adapter_document,
    build_vfc_adapter_document,
    build_vscsi_adapter_document,
)


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
    assert "IsTaggedVLAN" not in xml
    assert "MACAddress" not in xml
    assert "VirtualSwitchID" not in xml


def test_network_adapter_full():
    xml = build_client_network_adapter_document(
        port_vlan_id=200, slot_number=9, virtual_switch_id=3, tagged=True,
        mac_address="02:00:00:00:00:01",
    )
    assert ">200<" in xml
    assert "<VirtualSlotNumber" in xml and ">9<" in xml
    assert "<VirtualSwitchID" in xml and ">3<" in xml
    assert "IsTaggedVLAN" in xml and "true" in xml
    assert "02:00:00:00:00:01" in xml
