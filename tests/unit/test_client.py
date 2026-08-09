"""Tests for HMCClient against a mocked HMC (respx)."""

import warnings

import httpx
import pytest
import respx

from hmc_mcp.client import HMCClient, HMCError
from hmc_mcp.jobs import build_job_request

from conftest import make_config

BASE = "https://hmc.test:12443"

LOGON_RESPONSE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<LogonResponse xmlns="http://www.ibm.com/xmlns/systems/power/firmware/web/mc/2012_10/">
  <X-API-Session>test-session-token-123</X-API-Session>
</LogonResponse>
"""

LPAR_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:11111111-1111-1111-1111-111111111111</id>
    <title>LogicalPartition:lpar1</title>
    <link rel="SELF" href="{base}/rest/api/uom/LogicalPartition/11111111-1111-1111-1111-111111111111"/>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <LogicalPartition xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <PartitionName>lpar1</PartitionName>
        <PartitionState>running</PartitionState>
      </LogicalPartition>
    </content>
  </entry>
  <entry>
    <id>urn:uuid:22222222-2222-2222-2222-222222222222</id>
    <title>LogicalPartition:lpar2</title>
    <link rel="SELF" href="{base}/rest/api/uom/LogicalPartition/22222222-2222-2222-2222-222222222222"/>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <LogicalPartition xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <PartitionName>lpar2</PartitionName>
        <PartitionState>not activated</PartitionState>
      </LogicalPartition>
    </content>
  </entry>
</feed>
""".format(base=BASE)

QUICK_STATE = "running"

JOB_ENTRY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:job-uuid-999</id>
  <title>Job:PowerOn</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <Job xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <JobID>job-uuid-999</JobID>
      <Status>RUNNING</Status>
      <RequestedOperation>PowerOn</RequestedOperation>
    </Job>
  </content>
</entry>
"""



@pytest.fixture
def mock_hmc():
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        router.put("/rest/api/web/Logon").mock(
            return_value=httpx.Response(200, text=LOGON_RESPONSE)
        )
        router.delete("/rest/api/web/Logon").mock(return_value=httpx.Response(204))
        yield router


@pytest.mark.asyncio
async def test_logon_logoff(mock_hmc):
    async with HMCClient(make_config()) as hmc:
        assert hmc.is_logged_on
        assert hmc._session_token == "test-session-token-123"
    assert not hmc.is_logged_on



@pytest.mark.asyncio
async def test_logon_warns_when_verify_ssl_disabled(mock_hmc):
    """Logon with verify_ssl=False emits an explicit MITM warning."""
    with pytest.warns(UserWarning, match="certificate verification is disabled"):
        async with HMCClient(make_config(verify_ssl=False)):
            pass


@pytest.mark.asyncio
async def test_logon_silent_when_verify_ssl_enabled(mock_hmc):
    """Logon with verify_ssl=True emits no verification warning."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        async with HMCClient(make_config(verify_ssl=True)):
            pass
    assert not [w for w in caught if "verification" in str(w.message)]


@pytest.mark.asyncio
async def test_logon_failure(mock_hmc):
    mock_hmc.put("/rest/api/web/Logon").mock(
        return_value=httpx.Response(401, text="<error>bad credentials</error>")
    )
    with pytest.raises(HMCError) as exc_info:
        async with HMCClient(make_config()):
            pass
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_list_logical_partitions(mock_hmc):
    mock_hmc.get("/rest/api/uom/LogicalPartition").mock(
        return_value=httpx.Response(200, text=LPAR_FEED)
    )
    async with HMCClient(make_config()) as hmc:
        lpars = await hmc.list_logical_partitions()
    assert len(lpars) == 2
    assert lpars[0]["Resource"]["PartitionName"] == "lpar1"
    assert lpars[1]["Resource"]["PartitionState"] == "not activated"


@pytest.mark.asyncio
async def test_list_lpars_for_system(mock_hmc):
    mock_hmc.get(
        "/rest/api/uom/ManagedSystem/sys-uuid/LogicalPartition"
    ).mock(return_value=httpx.Response(200, text=LPAR_FEED))
    async with HMCClient(make_config()) as hmc:
        lpars = await hmc.list_logical_partitions("sys-uuid")
    assert len(lpars) == 2


@pytest.mark.asyncio
async def test_quick_property(mock_hmc):
    mock_hmc.get(
        "/rest/api/uom/LogicalPartition/lpar-uuid/quick/PartitionState"
    ).mock(return_value=httpx.Response(200, text=QUICK_STATE))
    async with HMCClient(make_config()) as hmc:
        state = await hmc.get_quick_property(
            "LogicalPartition", "lpar-uuid", "PartitionState"
        )
    assert state == "running"


@pytest.mark.asyncio
async def test_find_partition_by_name(mock_hmc):
    mock_hmc.get(
        "/rest/api/uom/LogicalPartition/search/(PartitionName==lpar2)"
    ).mock(return_value=httpx.Response(200, text=LPAR_FEED))
    async with HMCClient(make_config()) as hmc:
        found = await hmc.find_partition_by_name("lpar2")
    assert found is not None
    assert found["Resource"]["PartitionName"] == "lpar1"  # mock returns full feed


@pytest.mark.asyncio
async def test_submit_power_on_job(mock_hmc):
    route = mock_hmc.put(
        "/rest/api/uom/LogicalPartition/lpar-uuid/do/PowerOn"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))
    async with HMCClient(make_config()) as hmc:
        job = await hmc.submit_job(
            "/rest/api/uom/LogicalPartition/lpar-uuid/do/PowerOn",
            build_job_request("PowerOn", "LogicalPartition"),
        )
    assert route.called
    request = route.calls.last.request
    assert b"PowerOn" in request.content
    assert b"LogicalPartition" in request.content
    assert job is not None
    assert job["Resource"]["Status"] == "RUNNING"


@pytest.mark.asyncio
async def test_job_request_xml():
    xml = build_job_request("PowerOff", "LogicalPartition", {"immediate": "true"})
    assert "<OperationName" in xml and "PowerOff" in xml
    assert "<GroupName" in xml and "LogicalPartition" in xml
    assert "immediate" in xml
    assert "true" in xml


@pytest.mark.asyncio
async def test_missing_credentials():
    config = make_config(host="", user="", password="")
    with pytest.raises(ValueError, match="Missing HMC configuration"):
        HMCClient(config)


@pytest.mark.asyncio
async def test_http_error_raises(mock_hmc):
    mock_hmc.get("/rest/api/uom/ManagedSystem").mock(
        return_value=httpx.Response(500, text="<m><Message>boom</Message></m>")
    )
    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError) as exc_info:
            await hmc.list_managed_systems()
    assert exc_info.value.status_code == 500
    assert "boom" in str(exc_info.value)


CREATED_LPAR = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:new-lpar-uuid</id>
  <title>LogicalPartition:newlpar</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <LogicalPartition xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <PartitionName>newlpar</PartitionName>
      <PartitionState>not activated</PartitionState>
    </LogicalPartition>
  </content>
</entry>
"""


@pytest.mark.asyncio
async def test_create_logical_partition(mock_hmc):
    route = mock_hmc.put(
        "/rest/api/uom/ManagedSystem/sys-uuid/LogicalPartition"
    ).mock(return_value=httpx.Response(201, text=CREATED_LPAR))
    from hmc_mcp.documents import LparResources, build_lpar_document

    xml = build_lpar_document(
        name="newlpar",
        resources=LparResources(
            min_memory=256,
            desired_memory=4096,
            max_memory=8192,
            desired_vcpus=1,
            max_vcpus=2,
        ),
    )
    async with HMCClient(make_config()) as hmc:
        created = await hmc.create_logical_partition("sys-uuid", xml)
    assert route.called
    body = route.calls.last.request.content.decode()
    assert "newlpar" in body and "4096" in body
    assert created is not None
    assert created["Resource"]["PartitionName"] == "newlpar"


@pytest.mark.asyncio
async def test_modify_logical_partition(mock_hmc):
    route = mock_hmc.post("/rest/api/uom/LogicalPartition/lpar-uuid").mock(
        return_value=httpx.Response(200, text=CREATED_LPAR)
    )
    from hmc_mcp.documents import LparResources, build_lpar_document

    xml = build_lpar_document(name=None, resources=LparResources(desired_memory=2048))
    async with HMCClient(make_config()) as hmc:
        updated = await hmc.modify_logical_partition("lpar-uuid", xml)
    assert route.called
    assert "2048" in route.calls.last.request.content.decode()
    assert updated is not None


@pytest.mark.asyncio
async def test_delete_logical_partition(mock_hmc):
    route = mock_hmc.delete("/rest/api/uom/LogicalPartition/lpar-uuid").mock(
        return_value=httpx.Response(204)
    )
    async with HMCClient(make_config()) as hmc:
        await hmc.delete_logical_partition("lpar-uuid")
    assert route.called


ADAPTER_ENTRY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:adapter-uuid-1</id>
  <title>ClientNetworkAdapter</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <ClientNetworkAdapter xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <PortVLANID>100</PortVLANID>
      <VirtualSlotNumber>9</VirtualSlotNumber>
    </ClientNetworkAdapter>
  </content>
</entry>
"""


@pytest.mark.asyncio
async def test_add_network_adapter(mock_hmc):
    route = mock_hmc.put(
        "/rest/api/uom/LogicalPartition/lpar-uuid/ClientNetworkAdapter"
    ).mock(return_value=httpx.Response(201, text=ADAPTER_ENTRY))
    async with HMCClient(make_config()) as hmc:
        adapter = await hmc.add_network_adapter("lpar-uuid", port_vlan_id=100, slot_number=9)
    assert route.called
    body = route.calls.last.request.content.decode()
    assert "ClientNetworkAdapter" in body and ">100<" in body
    assert adapter is not None
    assert adapter["Resource"]["PortVLANID"] == "100"


@pytest.mark.asyncio
async def test_add_vscsi_adapter(mock_hmc):
    route = mock_hmc.put(
        "/rest/api/uom/LogicalPartition/lpar-uuid/VirtualSCSIClientAdapter"
    ).mock(return_value=httpx.Response(201, text=ADAPTER_ENTRY))
    async with HMCClient(make_config()) as hmc:
        await hmc.add_vscsi_adapter("lpar-uuid", vios_partition_id=1, vios_slot=5)
    body = route.calls.last.request.content.decode()
    assert "VirtualSCSIClientAdapter" in body
    assert "RemoteLogicalPartitionID" in body and "RemoteSlotNumber" in body


@pytest.mark.asyncio
async def test_add_vfc_adapter(mock_hmc):
    route = mock_hmc.put(
        "/rest/api/uom/LogicalPartition/lpar-uuid/VirtualFibreChannelClientAdapter"
    ).mock(return_value=httpx.Response(201, text=ADAPTER_ENTRY))
    async with HMCClient(make_config()) as hmc:
        await hmc.add_vfc_adapter("lpar-uuid", vios_partition_id=1, vios_slot=6)
    body = route.calls.last.request.content.decode()
    assert "VirtualFibreChannelClientAdapter" in body
    assert "ConnectingPartitionID" in body and "ConnectingVirtualSlotNumber" in body


@pytest.mark.asyncio
async def test_list_adapters(mock_hmc):
    mock_hmc.get(
        "/rest/api/uom/LogicalPartition/lpar-uuid/ClientNetworkAdapter"
    ).mock(return_value=httpx.Response(200, text=ADAPTER_ENTRY))
    async with HMCClient(make_config()) as hmc:
        adapters = await hmc.list_child("LogicalPartition", "lpar-uuid", "ClientNetworkAdapter")
    assert len(adapters) == 1
    assert adapters[0]["ResourceType"] == "ClientNetworkAdapter"


@pytest.mark.asyncio
async def test_delete_adapter(mock_hmc):
    route = mock_hmc.delete(
        "/rest/api/uom/LogicalPartition/lpar-uuid/ClientNetworkAdapter/adapter-uuid-1"
    ).mock(return_value=httpx.Response(204))
    async with HMCClient(make_config()) as hmc:
        await hmc.delete_child("LogicalPartition", "lpar-uuid", "ClientNetworkAdapter", "adapter-uuid-1")
    assert route.called


VG_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:vg-uuid-1</id>
    <title>VolumeGroup:vg_1</title>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <VolumeGroup xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <GroupName>vg_1</GroupName>
        <GroupCapacity>102400</GroupCapacity>
        <FreeSpace>51200</FreeSpace>
      </VolumeGroup>
    </content>
  </entry>
</feed>
"""

VG_ENTRY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:vg-uuid-1</id>
  <title>VolumeGroup:vg_1</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <VolumeGroup xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <GroupName>vg_1</GroupName>
    </VolumeGroup>
  </content>
</entry>
"""

VIOS_ENTRY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:vios-uuid-1</id>
  <title>VirtualIOServer:vios1</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <VirtualIOServer xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <PartitionName>vios1</PartitionName>
    </VirtualIOServer>
  </content>
</entry>
"""


@pytest.mark.asyncio
async def test_list_volume_groups(mock_hmc):
    mock_hmc.get("/rest/api/uom/VirtualIOServer/vios-uuid/VolumeGroup").mock(
        return_value=httpx.Response(200, text=VG_FEED)
    )
    async with HMCClient(make_config()) as hmc:
        vgs = await hmc.list_volume_groups("vios-uuid")
    assert len(vgs) == 1
    assert vgs[0]["Resource"]["GroupName"] == "vg_1"
    assert vgs[0]["Resource"]["FreeSpace"] == "51200"


@pytest.mark.asyncio
async def test_create_volume_group(mock_hmc):
    route = mock_hmc.put("/rest/api/uom/VirtualIOServer/vios-uuid/VolumeGroup").mock(
        return_value=httpx.Response(201, text=VG_ENTRY)
    )
    async with HMCClient(make_config()) as hmc:
        vg = await hmc.create_volume_group("vios-uuid", "vg_1", ["hdisk10"])
    assert route.called
    body = route.calls.last.request.content.decode()
    assert "vg_1" in body and "hdisk10" in body
    assert vg is not None


@pytest.mark.asyncio
async def test_create_virtual_disk(mock_hmc):
    route = mock_hmc.post(
        "/rest/api/uom/VirtualIOServer/vios-uuid/VolumeGroup/vg-uuid"
    ).mock(return_value=httpx.Response(200, text=VG_ENTRY))
    async with HMCClient(make_config()) as hmc:
        await hmc.create_virtual_disk("vios-uuid", "vg-uuid", "lv_boot", 51200)
    body = route.calls.last.request.content.decode()
    assert "VirtualDisks" in body and "lv_boot" in body and "51200" in body


@pytest.mark.asyncio
async def test_map_storage_to_lpar(mock_hmc):
    route = mock_hmc.post("/rest/api/uom/VirtualIOServer/vios-uuid").mock(
        return_value=httpx.Response(200, text=VIOS_ENTRY)
    )
    async with HMCClient(make_config()) as hmc:
        await hmc.map_storage_to_lpar(
            "vios-uuid", "VirtualDisk", "lv_boot", "lpar-uuid"
        )
    body = route.calls.last.request.content.decode()
    assert "VirtualSCSIMapping" in body
    assert "lv_boot" in body
    assert "LogicalPartition/lpar-uuid" in body


JOB_ENTRY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:job-uuid-1</id>
  <title>Job</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <Job xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <JobID>12345</JobID>
      <Status>RUNNING</Status>
    </Job>
  </content>
</entry>
"""

CLUSTER_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:cluster-uuid-1</id>
    <title>Cluster:cluster1</title>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <Cluster xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <ClusterName>cluster1</ClusterName>
      </Cluster>
    </content>
  </entry>
</feed>
"""


@pytest.mark.asyncio
async def test_list_clusters(mock_hmc):
    mock_hmc.get("/rest/api/uom/Cluster").mock(
        return_value=httpx.Response(200, text=CLUSTER_FEED)
    )
    async with HMCClient(make_config()) as hmc:
        clusters = await hmc.list_clusters()
    assert len(clusters) == 1
    assert clusters[0]["Resource"]["ClusterName"] == "cluster1"


@pytest.mark.asyncio
async def test_create_logical_unit(mock_hmc):
    route = mock_hmc.put(
        "/rest/api/uom/Cluster/cluster-uuid/do/CreateLogicalUnit"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))
    async with HMCClient(make_config()) as hmc:
        job = await hmc.create_logical_unit("cluster-uuid", "newLU", 18)
    body = route.calls.last.request.content.decode()
    assert "CreateLogicalUnit" in body and "newLU" in body and ">18<" in body
    assert job is not None and job["Resource"]["JobID"] == "12345"


@pytest.mark.asyncio
async def test_delete_logical_unit(mock_hmc):
    route = mock_hmc.put(
        "/rest/api/uom/Cluster/cluster-uuid/do/DeleteLogicalUnit"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))
    async with HMCClient(make_config()) as hmc:
        await hmc.delete_logical_unit("cluster-uuid", "udid-9")
    body = route.calls.last.request.content.decode()
    assert "DeleteLogicalUnit" in body and "udid-9" in body


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


@pytest.mark.asyncio
async def test_get_pcm_preferences(mock_hmc):
    mock_hmc.get("/rest/api/pcm/ManagedSystem/sys-uuid/preferences").mock(
        return_value=httpx.Response(200, text=PCM_PREFS_XML)
    )
    async with HMCClient(make_config()) as hmc:
        prefs = await hmc.get_pcm_preferences("ManagedSystem", "sys-uuid")
    assert prefs["LongTermMonitorEnabled"] is True
    assert prefs["AggregationEnabled"] is False


@pytest.mark.asyncio
async def test_set_pcm_preferences(mock_hmc):
    route = mock_hmc.post("/rest/api/pcm/ManagedSystem/sys-uuid/preferences").mock(
        return_value=httpx.Response(200, text=PCM_PREFS_XML)
    )
    async with HMCClient(make_config()) as hmc:
        prefs = await hmc.set_pcm_preferences(
            "ManagedSystem", "sys-uuid", LongTermMonitorEnabled=True
        )
    body = route.calls.last.request.content.decode()
    assert "LongTermMonitorEnabled" in body and ">true<" in body
    assert prefs["LongTermMonitorEnabled"] is True
    assert prefs["AggregationEnabled"] is False


@pytest.mark.asyncio
async def test_get_processed_metrics_links(mock_hmc):
    route = mock_hmc.get(
        "/rest/api/pcm/ManagedSystem/sys-uuid/ProcessedMetrics"
    ).mock(return_value=httpx.Response(200, text=PCM_FEED))
    async with HMCClient(make_config()) as hmc:
        links = await hmc.get_processed_metric_links(
            "ManagedSystem", "sys-uuid", "2026-08-07T11:00:00Z", no_of_samples=5
        )
    assert len(links) == 1
    assert links[0]["link"].endswith("_2.json")
    # StartTS/NoOfSamples were sent as query params on the metrics GET.
    req = route.calls.last.request
    assert "StartTS=" in str(req.url) and "NoOfSamples=5" in str(req.url)


@pytest.mark.asyncio
async def test_fetch_json(mock_hmc):
    mock_hmc.get("/rest/api/pcm/ProcessedMetrics/ManagedSystem_sys_2.json").mock(
        return_value=httpx.Response(200, json={"systemUtil": {"utilization": 0.5}})
    )
    async with HMCClient(make_config()) as hmc:
        data = await hmc.fetch_json("/rest/api/pcm/ProcessedMetrics/ManagedSystem_sys_2.json")
    assert data["systemUtil"]["utilization"] == 0.5


@pytest.mark.asyncio
async def test_fetch_json_404_raises(mock_hmc):
    """fetch_json raises HMCError on 404 like every other client method."""
    mock_hmc.get("/rest/api/pcm/ProcessedMetrics/ManagedSystem_sys_2.json").mock(
        return_value=httpx.Response(404, text="<error>expired</error>")
    )
    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError) as exc_info:
            await hmc.fetch_json("/rest/api/pcm/ProcessedMetrics/ManagedSystem_sys_2.json")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_parse_failure_names_failing_call(mock_hmc):
    """A 200 with malformed XML surfaces as HMCError naming the failing path."""
    mock_hmc.get("/rest/api/uom/ManagedSystem").mock(
        return_value=httpx.Response(200, text="<feed><entry>")
    )
    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError) as exc_info:
            await hmc.list_managed_systems()
    assert "Failed to parse /rest/api/uom/ManagedSystem response" in str(exc_info.value)


@pytest.mark.asyncio
async def test_pcm_parse_failure_names_failing_call(mock_hmc):
    """A 200 with malformed PCM preferences XML surfaces as HMCError."""
    mock_hmc.get("/rest/api/pcm/ManagedSystem/sys-uuid/preferences").mock(
        return_value=httpx.Response(
            200, text="<ManagementConsolePcmPreference><unclosed>"
        )
    )
    async with HMCClient(make_config()) as hmc:
        with pytest.raises(HMCError) as exc_info:
            await hmc.get_pcm_preferences("ManagedSystem", "sys-uuid")
    assert (
        "Failed to parse /rest/api/pcm/ManagedSystem/sys-uuid/preferences response"
        in str(exc_info.value)
    )
