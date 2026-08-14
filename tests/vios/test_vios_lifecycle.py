"""Tests for VIOS lifecycle tools: create, delete, install."""

import httpx
import pytest
from unittest.mock import AsyncMock, patch

from conftest import JOB_ENTRY, make_config

from hmc_mcp.client import HMCClient
from hmc_mcp.jobs import install_vios_job
from hmc_mcp.documents import LparResources, build_vios_document

BASE = "https://hmc.test:12443"

VIOS_ENTRY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:00000000-0000-0000-0000-000000000003</id>
  <title>LogicalPartition:vios1</title>
  <link rel="SELF" href="{base}/rest/api/uom/LogicalPartition/00000000-0000-0000-0000-000000000003"/>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <LogicalPartition xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <PartitionName>vios1</PartitionName>
      <PartitionType>Virtual IO Server</PartitionType>
      <PartitionState>not activated</PartitionState>
    </LogicalPartition>
  </content>
</entry>
""".format(base=BASE)


# ---------------------------------------------------------------------- #
# Unit: build_vios_document
# ---------------------------------------------------------------------- #


def test_build_vios_document_minimal():
    xml = build_vios_document(name="vios1")
    assert "Virtual IO Server" in xml
    assert "<PartitionName" in xml and "vios1" in xml
    assert "PartitionMemoryConfiguration" in xml
    assert "PartitionProcessorConfiguration" in xml
    assert "SharedProcessorConfiguration" in xml


def test_build_vios_document_custom_resources():
    xml = build_vios_document(
        name="vios2",
        resources=LparResources(
            min_memory=1024,
            desired_memory=8192,
            max_memory=16384,
            desired_vcpus=4,
            min_vcpus=2,
            max_vcpus=8,
            desired_procs=1.0,
            min_procs=0.5,
            max_procs=2.0,
            uncapped=True,
        ),
    )
    assert "vios2" in xml
    assert "Virtual IO Server" in xml
    assert "8192" in xml
    assert "16384" in xml
    assert "1024" in xml


# ---------------------------------------------------------------------- #
# Unit: install_vios_job
# ---------------------------------------------------------------------- #


def test_install_vios_job_xml():
    xml = install_vios_job(
        nim_ip="192.168.1.10",
        nim_gateway="192.168.1.1",
        nim_subnetmask="255.255.255.0",
        vios_ip="192.168.1.20",
        vlan_id="100",
        timeout=90,
    )
    assert "InstallVIOS" in xml
    assert "VirtualIOServer" in xml
    assert "192.168.1.10" in xml
    assert "192.168.1.1" in xml
    assert "255.255.255.0" in xml
    assert "192.168.1.20" in xml
    assert "100" in xml
    assert "90" in xml


def test_install_vios_job_default_timeout():
    xml = install_vios_job(
        nim_ip="10.0.0.1",
        nim_gateway="10.0.0.254",
        nim_subnetmask="255.255.255.0",
        vios_ip="10.0.0.5",
        vlan_id="0",
    )
    assert "60" in xml  # default timeout


# ---------------------------------------------------------------------- #
# Integration: client methods via respx mocks
# ---------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_create_vios(mock_hmc):
    """hmc_create_vios: PUT to the LPAR endpoint with VIOS XML."""
    route = mock_hmc.put("/rest/api/uom/ManagedSystem/sys-uuid/LogicalPartition").mock(
        return_value=httpx.Response(201, text=VIOS_ENTRY)
    )

    async with HMCClient(make_config()) as hmc:
        xml = build_vios_document(name="vios1")
        result = await hmc.create_logical_partition("sys-uuid", xml)

    assert route.called
    body = route.calls.last.request.content.decode()
    assert "Virtual IO Server" in body
    assert "vios1" in body
    assert result is not None


@pytest.mark.asyncio
async def test_delete_vios(mock_hmc):
    """hmc_delete_vios: DELETE to the LogicalPartition endpoint."""
    route = mock_hmc.delete(
        "/rest/api/uom/LogicalPartition/00000000-0000-0000-0000-000000000003"
    ).mock(return_value=httpx.Response(204))

    async with HMCClient(make_config()) as hmc:
        await hmc.delete_logical_partition("00000000-0000-0000-0000-000000000003")

    assert route.called


@pytest.mark.asyncio
async def test_install_vios(mock_hmc):
    """hmc_install_vios: PUT a JobRequest to the InstallVIOS do/ endpoint."""
    route = mock_hmc.put(
        "/rest/api/uom/VirtualIOServer/00000000-0000-0000-0000-000000000003/do/InstallVIOS"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))

    async with HMCClient(make_config()) as hmc:
        job_xml = install_vios_job(
            nim_ip="192.168.1.10",
            nim_gateway="192.168.1.1",
            nim_subnetmask="255.255.255.0",
            vios_ip="192.168.1.20",
            vlan_id="100",
        )
        job = await hmc.submit_job(
            "/rest/api/uom/VirtualIOServer/00000000-0000-0000-0000-000000000003/do/InstallVIOS",
            job_xml,
        )

    assert route.called
    body = route.calls.last.request.content.decode()
    assert "InstallVIOS" in body
    assert "192.168.1.10" in body
    assert job is not None


# ---------------------------------------------------------------------- #
# Tool-layer tests for hmc_install_vios (wait=True/False)
# ---------------------------------------------------------------------- #

JOB_ENTRY_COMPLETED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:job-uuid-999</id>
  <title>Job</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <Job xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <JobID>job-uuid-999</JobID>
      <Status>COMPLETED</Status>
    </Job>
  </content>
</entry>
"""


def _hmc_env(monkeypatch) -> None:
    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "test-password")


def test_install_vios_accepts_partition_name(monkeypatch, mock_hmc):
    """The public VIOS target is resolved before the install request."""
    from hmc_mcp.server import hmc_install_vios

    _hmc_env(monkeypatch)
    mock_hmc.put(
        "/rest/api/uom/VirtualIOServer/00000000-0000-0000-0000-000000000003/do/InstallVIOS"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))
    resolver = AsyncMock(return_value="00000000-0000-0000-0000-000000000003")

    with patch("hmc_mcp.server_vios.resolve_vios_uuid", resolver):
        hmc_install_vios(
            "vios1",
            nim_ip="192.168.1.10",
            nim_gateway="192.168.1.1",
            nim_subnetmask="255.255.255.0",
            vios_ip="192.168.1.20",
        )

    resolver.assert_awaited_once()
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "abc123")


def test_install_vios_tool_submits_job(monkeypatch, mock_hmc):
    """hmc_install_vios PUTs a JobRequest to the InstallVIOS do/ endpoint."""
    from hmc_mcp.server import hmc_install_vios

    _hmc_env(monkeypatch)
    route = mock_hmc.put(
        "/rest/api/uom/VirtualIOServer/00000000-0000-0000-0000-000000000003/do/InstallVIOS"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))
    result = hmc_install_vios(
        "00000000-0000-0000-0000-000000000003",
        nim_ip="192.168.1.10",
        nim_gateway="192.168.1.1",
        nim_subnetmask="255.255.255.0",
        vios_ip="192.168.1.20",
        vlan_id="100",
    )
    assert route.called
    body = route.calls.last.request.content.decode()
    assert "InstallVIOS" in body
    assert "192.168.1.10" in body
    assert result["Resource"]["JobID"] == "job-uuid-999"


def test_install_vios_tool_wait_true_polls_to_completion(monkeypatch, mock_hmc):
    """hmc_install_vios(wait=True) submits then polls until COMPLETED."""
    from hmc_mcp.server import hmc_install_vios

    _hmc_env(monkeypatch)
    submit_route = mock_hmc.put(
        "/rest/api/uom/VirtualIOServer/00000000-0000-0000-0000-000000000003/do/InstallVIOS"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))
    poll_route = mock_hmc.get("/rest/api/uom/Job/job-uuid-999").mock(
        return_value=httpx.Response(200, text=JOB_ENTRY_COMPLETED)
    )
    result = hmc_install_vios(
        "00000000-0000-0000-0000-000000000003",
        nim_ip="192.168.1.10",
        nim_gateway="192.168.1.1",
        nim_subnetmask="255.255.255.0",
        vios_ip="192.168.1.20",
        wait=True,
        timeout_seconds=60,
        poll_interval=0,
    )
    assert submit_route.called
    assert poll_route.called
    assert result["Resource"]["Status"] == "COMPLETED"
