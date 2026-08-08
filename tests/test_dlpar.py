"""Tests for DLPAR processor and memory hot-plug tools."""

import httpx
import pytest

from conftest import make_config

from hmc_mcp.client import HMCClient
from hmc_mcp.templates import (
    LparResources,
    build_dlpar_mem_document,
    build_dlpar_proc_document,
)

LPAR_UUID = "aaaa0000-0000-0000-0000-000000000001"

LPAR_ENTRY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:{uuid}</id>
  <title>LogicalPartition:lpar1</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <LogicalPartition xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <PartitionName>lpar1</PartitionName>
      <PartitionState>running</PartitionState>
    </LogicalPartition>
  </content>
</entry>
""".format(uuid=LPAR_UUID)


# ------------------------------------------------------------------ #
# build_dlpar_proc_document unit tests
# ------------------------------------------------------------------ #


def test_dlpar_proc_shared_desired():
    xml = build_dlpar_proc_document(
        LparResources(desired_procs=0.5, desired_vcpus=1)
    )
    assert "<LogicalPartition" in xml
    assert "PartitionProcessorConfiguration" in xml
    assert "SharedProcessorConfiguration" in xml
    assert "DesiredProcessingUnits" in xml and ">0.5<" in xml
    assert "DesiredVirtualProcessors" in xml and ">1<" in xml
    # Must NOT contain memory section
    assert "PartitionMemoryConfiguration" not in xml
    assert "PartitionType" not in xml


def test_dlpar_proc_dedicated():
    xml = build_dlpar_proc_document(
        LparResources(desired_procs=2, min_procs=1, max_procs=4, dedicated=True)
    )
    assert "DedicatedProcessorConfiguration" in xml
    assert "<DesiredProcessors" in xml and ">2<" in xml
    assert "<MinimumProcessors" in xml and ">1<" in xml
    assert "<MaximumProcessors" in xml and ">4<" in xml
    assert "PartitionMemoryConfiguration" not in xml


def test_dlpar_proc_no_args_emits_metadata():
    xml = build_dlpar_proc_document()
    assert "<LogicalPartition" in xml
    assert "<Metadata><Atom/></Metadata>" in xml
    assert "PartitionProcessorConfiguration" not in xml


# ------------------------------------------------------------------ #
# build_dlpar_mem_document unit tests
# ------------------------------------------------------------------ #


def test_dlpar_mem_desired():
    xml = build_dlpar_mem_document(
        LparResources(desired_memory=2048, min_memory=512, max_memory=4096)
    )
    assert "<LogicalPartition" in xml
    assert "PartitionMemoryConfiguration" in xml
    assert "<DesiredMemory" in xml and ">2048<" in xml
    assert "<MinimumMemory" in xml and ">512<" in xml
    assert "<MaximumMemory" in xml and ">4096<" in xml
    # Must NOT contain processor or partition-type sections
    assert "PartitionProcessorConfiguration" not in xml
    assert "PartitionType" not in xml


def test_dlpar_mem_no_args_emits_metadata():
    xml = build_dlpar_mem_document()
    assert "<LogicalPartition" in xml
    assert "<Metadata><Atom/></Metadata>" in xml
    assert "PartitionMemoryConfiguration" not in xml


# ------------------------------------------------------------------ #
# HMCClient integration tests (respx-mocked)
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_dlpar_proc_posts_correct_xml(mock_hmc):
    """modify_logical_partition with a proc-only document hits the right endpoint."""
    route = mock_hmc.post(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}"
    ).mock(return_value=httpx.Response(200, text=LPAR_ENTRY))

    xml = build_dlpar_proc_document(
        LparResources(desired_procs=1.0, desired_vcpus=2)
    )
    async with HMCClient(make_config()) as hmc:
        result = await hmc.modify_logical_partition(LPAR_UUID, xml)

    assert route.called
    body = route.calls.last.request.content.decode()
    assert "PartitionProcessorConfiguration" in body
    assert "PartitionMemoryConfiguration" not in body
    assert "PartitionType" not in body
    assert result is not None


@pytest.mark.asyncio
async def test_dlpar_mem_posts_correct_xml(mock_hmc):
    """modify_logical_partition with a mem-only document hits the right endpoint."""
    route = mock_hmc.post(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}"
    ).mock(return_value=httpx.Response(200, text=LPAR_ENTRY))

    xml = build_dlpar_mem_document(
        LparResources(desired_memory=4096, min_memory=1024, max_memory=8192)
    )
    async with HMCClient(make_config()) as hmc:
        result = await hmc.modify_logical_partition(LPAR_UUID, xml)

    assert route.called
    body = route.calls.last.request.content.decode()
    assert "PartitionMemoryConfiguration" in body
    assert ">4096<" in body
    assert "PartitionProcessorConfiguration" not in body
    assert "PartitionType" not in body
    assert result is not None


@pytest.mark.asyncio
async def test_dlpar_proc_dedicated_posts_correct_xml(mock_hmc):
    """Dedicated processor DLPAR sends DedicatedProcessorConfiguration."""
    route = mock_hmc.post(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}"
    ).mock(return_value=httpx.Response(200, text=LPAR_ENTRY))

    xml = build_dlpar_proc_document(
        LparResources(desired_procs=2, dedicated=True)
    )
    async with HMCClient(make_config()) as hmc:
        await hmc.modify_logical_partition(LPAR_UUID, xml)

    body = route.calls.last.request.content.decode()
    assert "DedicatedProcessorConfiguration" in body
    assert "HasDedicatedProcessors" in body and ">true<" in body
