"""Tests for the Atom/XML parsing helpers."""

from hmc_mcp.xmlutil import element_to_dict, find_text, localname, parse_feed

MANAGED_SYSTEM_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:feed-1</id>
  <title>ManagedSystem</title>
  <entry>
    <id>urn:uuid:aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee</id>
    <title>ManagedSystem:9179-MHD*06064FV</title>
    <link rel="SELF" href="https://hmc.example.com:12443/rest/api/uom/ManagedSystem/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"/>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <ManagedSystem xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/" kb="CUR" kxe="false" schemaVersion="V1_0">
        <Metadata><Atom/></Metadata>
        <SystemName kb="CUR" kxe="false">server1</SystemName>
        <State kb="CUR" kxe="false">operating</State>
        <MachineTypeModelSerialNumber kb="CUR" kxe="false">
          <MachineType kb="CUR" kxe="false">9179</MachineType>
          <Model kb="CUR" kxe="false">MHD</Model>
          <SerialNumber kb="CUR" kxe="false">06064FV</SerialNumber>
        </MachineTypeModelSerialNumber>
      </ManagedSystem>
    </content>
  </entry>
  <entry>
    <id>urn:uuid:11111111-2222-3333-4444-555555555555</id>
    <title>ManagedSystem:7042-CR8*212345A</title>
    <link rel="SELF" href="https://hmc.example.com:12443/rest/api/uom/ManagedSystem/11111111-2222-3333-4444-555555555555"/>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <ManagedSystem xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/" kb="CUR" kxe="false" schemaVersion="V1_0">
        <Metadata><Atom/></Metadata>
        <SystemName kb="CUR" kxe="false">server2</SystemName>
        <State kb="CUR" kxe="false">operating</State>
      </ManagedSystem>
    </content>
  </entry>
</feed>
"""

SINGLE_ENTRY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:lpar-uuid-1</id>
  <title>LogicalPartition:mylpar</title>
  <link rel="SELF" href="https://hmc.example.com:12443/rest/api/uom/LogicalPartition/lpar-uuid-1"/>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <LogicalPartition xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <PartitionName>mylpar</PartitionName>
      <PartitionID>3</PartitionID>
      <PartitionState>running</PartitionState>
      <PartitionType>AIX/Linux</PartitionType>
    </LogicalPartition>
  </content>
</entry>
"""


def test_localname():
    assert localname("{http://www.w3.org/2005/Atom}entry") == "entry"
    assert localname("SystemName") == "SystemName"


def test_parse_feed_multiple_entries():
    entries = parse_feed(MANAGED_SYSTEM_FEED)
    assert len(entries) == 2

    first = entries[0]
    assert first["UUID"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert first["ResourceType"] == "ManagedSystem"
    assert first["link"].endswith("/rest/api/uom/ManagedSystem/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    assert first["Resource"]["SystemName"] == "server1"
    assert first["Resource"]["State"] == "operating"
    mtms = first["Resource"]["MachineTypeModelSerialNumber"]
    assert mtms["MachineType"] == "9179"
    assert mtms["Model"] == "MHD"
    assert mtms["SerialNumber"] == "06064FV"

    assert entries[1]["Resource"]["SystemName"] == "server2"


def test_parse_single_entry():
    entries = parse_feed(SINGLE_ENTRY)
    assert len(entries) == 1
    lpar = entries[0]["Resource"]
    assert lpar["PartitionName"] == "mylpar"
    assert lpar["PartitionID"] == "3"
    assert lpar["PartitionState"] == "running"


def test_find_text():
    xml = '<r xmlns="x"><X-API-Session>tok123</X-API-Session></r>'
    assert find_text(xml, "X-API-Session") == "tok123"
    assert find_text(xml, "Nope") is None


def test_repeated_children_become_list():
    xml = '<r><item>1</item><item>2</item><item>3</item></r>'
    result = element_to_dict(__import__("xml.etree.ElementTree", fromlist=["fromstring"]).fromstring(xml))
    assert result["item"] == ["1", "2", "3"]
