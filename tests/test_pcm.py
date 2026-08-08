"""Tests for Cluster/SSP job builders and PCM helpers."""

from hmc_mcp.jobs import create_logical_unit_job, delete_logical_unit_job
from hmc_mcp.pcm import (
    build_pcm_preferences_document,
    metric_links,
    pcm_preferences_to_dict,
)


def test_create_logical_unit_job():
    xml = create_logical_unit_job("newLU", 18, "THIN", "VirtualIO_Disk")
    assert "CreateLogicalUnit" in xml
    assert "Cluster" in xml
    assert "<ParameterName" in xml and "LUName" in xml and "newLU" in xml
    assert "LUSize" in xml and "18" in xml
    assert "LUType" in xml and "THIN" in xml
    assert "DeviceType" in xml and "VirtualIO_Disk" in xml
    assert "ClonedFrom" not in xml  # omitted when not cloning


def test_create_logical_unit_job_clone():
    xml = create_logical_unit_job("cloneLU", 20, cloned_from="udid-src")
    assert "ClonedFrom" in xml and "udid-src" in xml


def test_delete_logical_unit_job():
    xml = delete_logical_unit_job("udid-123")
    assert "DeleteLogicalUnit" in xml
    assert "LogicalUnitUDID" in xml and "udid-123" in xml


def test_pcm_preferences_document():
    xml = build_pcm_preferences_document(LongTermMonitorEnabled=True, AggregationEnabled=False)
    assert "LongTermMonitorEnabled" in xml and ">true<" in xml
    assert "AggregationEnabled" in xml and ">false<" in xml
    assert "ShortTermMonitorEnabled" not in xml  # only specified flags


def test_pcm_preferences_parse():
    xml = """<?xml version="1.0"?>
<ManagementConsolePcmPreference xmlns="http://www.ibm.com/xmlns/systems/power/firmware/pcm/mc/2012_10/">
  <LongTermMonitorEnabled>true</LongTermMonitorEnabled>
  <AggregationEnabled>false</AggregationEnabled>
  <EnergyMonitoringCapable>true</EnergyMonitoringCapable>
</ManagementConsolePcmPreference>
"""
    prefs = pcm_preferences_to_dict(xml)
    assert prefs["LongTermMonitorEnabled"] is True
    assert prefs["AggregationEnabled"] is False
    assert prefs["EnergyMonitoringCapable"] is True


def test_metric_links():
    feed = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>ManagedSystem ProcessedMetrics</title>
    <updated>2026-08-07T12:00:00Z</updated>
    <link rel="SELF" href="/rest/api/pcm/ProcessedMetrics/ManagedSystem_abc_1.json" type="application/json"/>
  </entry>
  <entry>
    <title>ManagedSystem ProcessedMetrics</title>
    <updated>2026-08-07T12:00:30Z</updated>
    <link rel="SELF" href="/rest/api/pcm/ProcessedMetrics/ManagedSystem_abc_2.json" type="application/json"/>
  </entry>
</feed>
"""
    links = metric_links(feed)
    assert len(links) == 2
    assert links[0]["link"].endswith("_1.json")
    assert links[1]["updated"] == "2026-08-07T12:00:30Z"
