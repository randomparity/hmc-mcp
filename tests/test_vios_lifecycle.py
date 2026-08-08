"""Tests for VIOS lifecycle tools: create, delete, install."""

import httpx
import pytest

from conftest import JOB_ENTRY, make_config

from hmc_mcp.client import HMCClient
from hmc_mcp.jobs import vios_install_job
from hmc_mcp.templates import build_vios_document

BASE = "https://hmc.test:12443"

VIOS_ENTRY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:vios-uuid-001</id>
  <title>LogicalPartition:vios1</title>
  <link rel="SELF" href="{base}/rest/api/uom/LogicalPartition/vios-uuid-001"/>
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
        min_memory=1024,
        desired_memory=8192,
        max_memory=16384,
        desired_vcpus=4,
        min_vcpus=2,
        max_vcpus=8,
        desired_procs=1.0,
        min_procs=0.5,
        max_procs=2.0,
    )
    assert "vios2" in xml
    assert "Virtual IO Server" in xml
    assert "8192" in xml
    assert "16384" in xml
    assert "1024" in xml


# ---------------------------------------------------------------------- #
# Unit: vios_install_job
# ---------------------------------------------------------------------- #


def test_vios_install_job_xml():
    xml = vios_install_job(
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


def test_vios_install_job_default_timeout():
    xml = vios_install_job(
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
    route = mock_hmc.put(
        "/rest/api/uom/ManagedSystem/sys-uuid/LogicalPartition"
    ).mock(return_value=httpx.Response(201, text=VIOS_ENTRY))

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
        "/rest/api/uom/LogicalPartition/vios-uuid-001"
    ).mock(return_value=httpx.Response(204))

    async with HMCClient(make_config()) as hmc:
        await hmc.delete_logical_partition("vios-uuid-001")

    assert route.called


@pytest.mark.asyncio
async def test_install_vios(mock_hmc):
    """hmc_install_vios: PUT a JobRequest to the InstallVIOS do/ endpoint."""
    route = mock_hmc.put(
        "/rest/api/uom/VirtualIOServer/vios-uuid-001/do/InstallVIOS"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))

    async with HMCClient(make_config()) as hmc:
        job_xml = vios_install_job(
            nim_ip="192.168.1.10",
            nim_gateway="192.168.1.1",
            nim_subnetmask="255.255.255.0",
            vios_ip="192.168.1.20",
            vlan_id="100",
        )
        job = await hmc.submit_job(
            "/rest/api/uom/VirtualIOServer/vios-uuid-001/do/InstallVIOS",
            job_xml,
        )

    assert route.called
    body = route.calls.last.request.content.decode()
    assert "InstallVIOS" in body
    assert "192.168.1.10" in body
    assert job is not None
