"""Tests for the hmc_lpar_summary composite tool.

Exercises the tool against a mocked HMC (respx) so the URL mapping and
field-extraction logic in server_composite.py is verified without a live HMC.
"""

from __future__ import annotations

import httpx
import pytest

from hmc_mcp.server import hmc_lpar_summary

LPAR_UUID = "aabbccdd-1234-5678-abcd-000000000001"
ADAPTER1_UUID = "aabbccdd-1234-5678-abcd-000000000002"
ADAPTER2_UUID = "aabbccdd-1234-5678-abcd-000000000003"

NS = "http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/"


def _hmc_env(monkeypatch) -> None:
    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "abc123")


def _lpar_feed(**fields: str) -> str:
    body = "\n".join(
        f'        <{k} xmlns="{NS}">{v}</{k}>'
        for k, v in fields.items()
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:{LPAR_UUID}</id>
    <title>LogicalPartition:{LPAR_UUID}</title>
    <link rel="SELF" href="https://hmc.test:12443/rest/api/uom/LogicalPartition/{LPAR_UUID}"/>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <LogicalPartition xmlns="{NS}">
{body}
      </LogicalPartition>
    </content>
  </entry>
</feed>
"""


def _adapter_feed(uuids: list[str]) -> str:
    entries = []
    for uuid in uuids:
        entries.append(f"""  <entry>
    <id>urn:uuid:{uuid}</id>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <ClientNetworkAdapter xmlns="{NS}">
        <AdapterID>1</AdapterID>
      </ClientNetworkAdapter>
    </content>
  </entry>""")
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
{"".join(entries)}
</feed>
"""


EMPTY_FEED = """\
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom"/>
"""

EMPTY_ADAPTER_FEED = EMPTY_FEED


# ---------------------------------------------------------------------- #
# Core happy-path
# ---------------------------------------------------------------------- #


def test_lpar_summary_by_uuid_returns_flat_dict(monkeypatch, mock_hmc):
    """hmc_lpar_summary(uuid) fetches LPAR + adapters and returns summary."""
    _hmc_env(monkeypatch)
    mock_hmc.get(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}").mock(
        return_value=httpx.Response(
            200,
            text=_lpar_feed(
                PartitionName="aix-prod",
                PartitionState="running",
                ResourceMonitoringControlState="active",
                PartitionType="AIX/Linux",
                PartitionID="3",
                DesiredMemory="8192",
                DesiredProcessingUnits="1.0",
                DesiredVirtualProcessors="2",
                OperatingSystemVersion="AIX 7.2",
                OperatingSystemType="AIX",
                Description="Production LPAR",
            ),
        )
    )
    mock_hmc.get(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/ClientNetworkAdapter"
    ).mock(
        return_value=httpx.Response(
            200, text=_adapter_feed([ADAPTER1_UUID, ADAPTER2_UUID])
        )
    )

    result = hmc_lpar_summary(LPAR_UUID)

    assert result["uuid"] == LPAR_UUID
    assert result["name"] == "aix-prod"
    assert result["state"] == "running"
    assert result["rmc_state"] == "active"
    assert result["partition_type"] == "AIX/Linux"
    assert result["partition_id"] == "3"
    assert result["desired_memory_mb"] == "8192"
    assert result["desired_proc_units"] == "1.0"
    assert result["desired_vcpus"] == "2"
    assert result["os_version"] == "AIX 7.2"
    assert result["os_type"] == "AIX"
    assert result["client_network_adapter_count"] == 2
    assert result["description"] == "Production LPAR"
    assert result["mapped_storage"] is None


def test_lpar_summary_no_adapters(monkeypatch, mock_hmc):
    """hmc_lpar_summary returns adapter_count=0 when the adapter feed is empty."""
    _hmc_env(monkeypatch)
    mock_hmc.get(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}").mock(
        return_value=httpx.Response(
            200, text=_lpar_feed(PartitionName="minimal", PartitionState="not activated")
        )
    )
    mock_hmc.get(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/ClientNetworkAdapter"
    ).mock(return_value=httpx.Response(200, text=EMPTY_ADAPTER_FEED))

    result = hmc_lpar_summary(LPAR_UUID)

    assert result["client_network_adapter_count"] == 0
    assert result["state"] == "not activated"


def test_lpar_summary_by_name_resolves_uuid(monkeypatch, mock_hmc):
    """hmc_lpar_summary('myname') resolves via search then fetches summary."""
    _hmc_env(monkeypatch)
    # Name resolution: search → UUID
    search_feed = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:{LPAR_UUID}</id>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <LogicalPartition xmlns="{NS}">
        <PartitionName>myname</PartitionName>
      </LogicalPartition>
    </content>
  </entry>
</feed>
"""
    mock_hmc.get(
        "/rest/api/uom/LogicalPartition/search/(PartitionName==myname)"
    ).mock(return_value=httpx.Response(200, text=search_feed))

    mock_hmc.get(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}").mock(
        return_value=httpx.Response(
            200,
            text=_lpar_feed(PartitionName="myname", PartitionState="running"),
        )
    )
    mock_hmc.get(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/ClientNetworkAdapter"
    ).mock(return_value=httpx.Response(200, text=EMPTY_ADAPTER_FEED))

    result = hmc_lpar_summary("myname")

    assert result["name"] == "myname"
    assert result["state"] == "running"
    assert result["uuid"] == LPAR_UUID


def test_lpar_summary_name_not_found_raises(monkeypatch, mock_hmc):
    """hmc_lpar_summary raises ValueError when the partition name is unknown."""
    _hmc_env(monkeypatch)
    mock_hmc.get(
        "/rest/api/uom/LogicalPartition/search/(PartitionName==ghost)"
    ).mock(return_value=httpx.Response(200, text=EMPTY_FEED))

    with pytest.raises(ValueError, match="ghost"):
        hmc_lpar_summary("ghost")


def test_lpar_summary_missing_optional_fields(monkeypatch, mock_hmc):
    """hmc_lpar_summary tolerates an LPAR entry that is missing optional fields."""
    _hmc_env(monkeypatch)
    mock_hmc.get(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}").mock(
        return_value=httpx.Response(
            200, text=_lpar_feed(PartitionName="bare", PartitionState="not activated")
        )
    )
    mock_hmc.get(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/ClientNetworkAdapter"
    ).mock(return_value=httpx.Response(200, text=EMPTY_ADAPTER_FEED))

    result = hmc_lpar_summary(LPAR_UUID)

    assert result["name"] == "bare"
    assert result["os_version"] is None
    assert result["description"] is None
    assert result["rmc_state"] is None
    assert result["mapped_storage"] is None
