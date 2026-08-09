"""Tests for Cluster/SSP job builders, PCM helpers, and metrics tools."""

import httpx
import pytest
from defusedxml import ElementTree as ET

from hmc_mcp.client import HMCError
from hmc_mcp.jobs import create_logical_unit_job, delete_logical_unit_job
from hmc_mcp.pcm import (
    build_pcm_preferences_document,
    metric_links,
    newest_metric_link,
    pcm_preferences_to_dict,
)
from hmc_mcp.server import (
    hmc_get_aggregated_metric_links,
    hmc_get_aggregated_metrics,
    hmc_get_pcm_preferences,
    hmc_get_processed_metric_links,
    hmc_get_processed_metrics,
    hmc_set_pcm_preferences,
)

PCM_PREFS_XML = """<?xml version="1.0"?>
<ManagementConsolePcmPreference xmlns="http://www.ibm.com/xmlns/systems/power/firmware/pcm/mc/2012_10/">
  <LongTermMonitorEnabled>true</LongTermMonitorEnabled>
  <AggregationEnabled>false</AggregationEnabled>
</ManagementConsolePcmPreference>
"""

PCM_FEED = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>ManagedSystem ProcessedMetrics</title>
    <updated>2026-08-07T12:00:30Z</updated>
    <link rel="SELF" href="/rest/api/pcm/ProcessedMetrics/ManagedSystem_sys_2.json" type="application/json"/>
  </entry>
</feed>
"""

EMPTY_FEED = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
</feed>
"""

# Newest entry (_2.json, 12:00:30) listed FIRST — the HMC does not guarantee
# the feed is ordered by age, so selection must be by updated stamp, not row.
OUT_OF_ORDER_FEED = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>ManagedSystem ProcessedMetrics</title>
    <updated>2026-08-07T12:00:30Z</updated>
    <link rel="SELF" href="/rest/api/pcm/ProcessedMetrics/ManagedSystem_sys_2.json" type="application/json"/>
  </entry>
  <entry>
    <title>ManagedSystem ProcessedMetrics</title>
    <updated>2026-08-07T12:00:00Z</updated>
    <link rel="SELF" href="/rest/api/pcm/ProcessedMetrics/ManagedSystem_sys_1.json" type="application/json"/>
  </entry>
</feed>
"""

METRICS_JSON = {"systemUtil": {"utilization": 0.5}, "sampleTime": "2026-08-07T12:00:30Z"}


def _hmc_env(monkeypatch):
    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "abc123")


def _route_metrics_feed(router, category, uuid, kind, text=PCM_FEED):
    router.get(f"/rest/api/pcm/{category}/{uuid}/{kind}").mock(
        return_value=httpx.Response(200, text=text)
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


def test_pcm_preferences_parse_malformed_raises():
    """Malformed XML propagates ParseError instead of silently returning {}."""
    with pytest.raises(ET.ParseError):
        pcm_preferences_to_dict("<ManagementConsolePcmPreference><unclosed>")


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


def test_metric_links_malformed_raises():
    """Malformed XML propagates ParseError instead of silently returning []."""
    with pytest.raises(ET.ParseError):
        metric_links("<feed><entry><unclosed>")


def test_newest_metric_link_selects_by_updated_not_position():
    """newest_metric_link returns the newest stamp regardless of feed order."""
    links = [
        {"link": "/old.json", "updated": "2026-08-07T12:00:00Z", "title": ""},
        {"link": "/new.json", "updated": "2026-08-07T12:00:30Z", "title": ""},
        {"link": "/mid.json", "updated": "2026-08-07T12:00:10Z", "title": ""},
    ]
    assert newest_metric_link(links)["link"] == "/new.json"


def test_newest_metric_link_unparseable_stamp_sorts_oldest():
    """A stamp that fails to parse never wins over a real timestamp."""
    links = [
        {"link": "/real.json", "updated": "2026-08-07T12:00:00Z", "title": ""},
        {"link": "/garbage.json", "updated": "not-a-date", "title": ""},
        {"link": "/missing.json", "updated": "", "title": ""},
    ]
    assert newest_metric_link(links)["link"] == "/real.json"


# ---------------------------------------------------------------------- #
# Metrics MCP tools (split link-list vs fetch)
# ---------------------------------------------------------------------- #


def test_get_processed_metric_links(monkeypatch, mock_hmc):
    """hmc_get_processed_metric_links returns the parsed link list."""
    _hmc_env(monkeypatch)
    _route_metrics_feed(mock_hmc, "ManagedSystem", "sys-uuid", "ProcessedMetrics")

    result = hmc_get_processed_metric_links(
        "ManagedSystem", "sys-uuid", "2026-08-07T11:00:00Z", no_of_samples=5
    )

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["link"].endswith("_2.json")


def test_get_processed_metrics_fetches_latest(monkeypatch, mock_hmc):
    """hmc_get_processed_metrics downloads the most recent metrics JSON."""
    _hmc_env(monkeypatch)
    _route_metrics_feed(mock_hmc, "ManagedSystem", "sys-uuid", "ProcessedMetrics")
    mock_hmc.get(
        "/rest/api/pcm/ProcessedMetrics/ManagedSystem_sys_2.json"
    ).mock(return_value=httpx.Response(200, json=METRICS_JSON))

    result = hmc_get_processed_metrics(
        "ManagedSystem", "sys-uuid", "2026-08-07T11:00:00Z"
    )

    assert result == METRICS_JSON


def test_get_processed_metrics_fetches_newest_not_last(monkeypatch, mock_hmc):
    """The newest document is selected by updated stamp, not feed position.

    The newest entry is listed first; the stale document (last row) returns a
    404. The tool must fetch the newest and return its JSON rather than
    surfacing no-data from the stale row.
    """
    _hmc_env(monkeypatch)
    _route_metrics_feed(
        mock_hmc,
        "ManagedSystem",
        "sys-uuid",
        "ProcessedMetrics",
        text=OUT_OF_ORDER_FEED,
    )
    mock_hmc.get(
        "/rest/api/pcm/ProcessedMetrics/ManagedSystem_sys_2.json"
    ).mock(return_value=httpx.Response(200, json=METRICS_JSON))
    mock_hmc.get(
        "/rest/api/pcm/ProcessedMetrics/ManagedSystem_sys_1.json"
    ).mock(return_value=httpx.Response(404, text="<error>expired</error>"))

    result = hmc_get_processed_metrics(
        "ManagedSystem", "sys-uuid", "2026-08-07T11:00:00Z"
    )

    assert result == METRICS_JSON


def test_get_processed_metrics_empty_feed(monkeypatch, mock_hmc):
    """hmc_get_processed_metrics returns {} when no metrics are in range."""
    _hmc_env(monkeypatch)
    _route_metrics_feed(
        mock_hmc, "ManagedSystem", "sys-uuid", "ProcessedMetrics", text=EMPTY_FEED
    )

    result = hmc_get_processed_metrics(
        "ManagedSystem", "sys-uuid", "2026-08-07T11:00:00Z"
    )

    assert result == {}


def test_get_processed_metrics_expired_doc(monkeypatch, mock_hmc):
    """A 404 on the metrics document (aged out of retention) surfaces as {}."""
    _hmc_env(monkeypatch)
    _route_metrics_feed(mock_hmc, "ManagedSystem", "sys-uuid", "ProcessedMetrics")
    mock_hmc.get(
        "/rest/api/pcm/ProcessedMetrics/ManagedSystem_sys_2.json"
    ).mock(return_value=httpx.Response(404, text="<error>expired</error>"))

    result = hmc_get_processed_metrics(
        "ManagedSystem", "sys-uuid", "2026-08-07T11:00:00Z"
    )

    assert result == {}


def test_get_processed_metrics_non_404_error_propagates(monkeypatch, mock_hmc):
    """A non-404 HMCError from the document fetch is re-raised, not swallowed."""
    _hmc_env(monkeypatch)
    _route_metrics_feed(mock_hmc, "ManagedSystem", "sys-uuid", "ProcessedMetrics")
    mock_hmc.get(
        "/rest/api/pcm/ProcessedMetrics/ManagedSystem_sys_2.json"
    ).mock(return_value=httpx.Response(500, text="<error>boom</error>"))

    with pytest.raises(HMCError):
        hmc_get_processed_metrics(
            "ManagedSystem", "sys-uuid", "2026-08-07T11:00:00Z"
        )


def test_get_aggregated_metric_links(monkeypatch, mock_hmc):
    """hmc_get_aggregated_metric_links uses the AggregatedMetrics endpoint."""
    _hmc_env(monkeypatch)
    _route_metrics_feed(mock_hmc, "LogicalPartition", "lpar-uuid", "AggregatedMetrics")

    result = hmc_get_aggregated_metric_links(
        "LogicalPartition", "lpar-uuid", "2026-08-07T11:00:00Z"
    )

    assert len(result) == 1
    assert result[0]["link"].endswith("_2.json")


def test_get_aggregated_metrics_fetches_latest(monkeypatch, mock_hmc):
    """hmc_get_aggregated_metrics downloads the most recent aggregated JSON."""
    _hmc_env(monkeypatch)
    _route_metrics_feed(mock_hmc, "LogicalPartition", "lpar-uuid", "AggregatedMetrics")
    mock_hmc.get(
        "/rest/api/pcm/ProcessedMetrics/ManagedSystem_sys_2.json"
    ).mock(return_value=httpx.Response(200, json=METRICS_JSON))

    result = hmc_get_aggregated_metrics(
        "LogicalPartition", "lpar-uuid", "2026-08-07T11:00:00Z"
    )

    assert result == METRICS_JSON


def test_get_pcm_preferences(monkeypatch, mock_hmc):
    """hmc_get_pcm_preferences returns the parsed preferences dict."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/pcm/ManagedSystem/sys-uuid/preferences").mock(
        return_value=httpx.Response(200, text=PCM_PREFS_XML)
    )

    result = hmc_get_pcm_preferences("ManagedSystem", "sys-uuid")

    assert result["LongTermMonitorEnabled"] is True
    assert result["AggregationEnabled"] is False


def test_set_pcm_preferences_returns_updated(monkeypatch, mock_hmc):
    """hmc_set_pcm_preferences returns the updated preferences dict."""
    _hmc_env(monkeypatch)
    mock_hmc.post("/rest/api/pcm/ManagedSystem/sys-uuid/preferences").mock(
        return_value=httpx.Response(200, text=PCM_PREFS_XML)
    )

    result = hmc_set_pcm_preferences("ManagedSystem", "sys-uuid", long_term_monitor=True)

    assert result["LongTermMonitorEnabled"] is True
    assert result["AggregationEnabled"] is False


def test_set_pcm_preferences_no_flags_raises(monkeypatch, mock_hmc):
    """hmc_set_pcm_preferences raises ValueError when no flags are supplied."""
    _hmc_env(monkeypatch)

    with pytest.raises(ValueError, match="No preference flags"):
        hmc_set_pcm_preferences("ManagedSystem", "sys-uuid")
