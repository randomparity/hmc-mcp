"""Tests for hmc_modify_system: template builder, client method, and MCP tool."""

import httpx
import pytest

from conftest import make_config

from hmc_mcp.client import HMCClient
from hmc_mcp.documents import build_managed_system_document

SYSTEM_ENTRY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:sys-uuid-1</id>
  <title>ManagedSystem:newsysname</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <ManagedSystem xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <SystemName>newsysname</SystemName>
      <PowerOffPolicy>autooff</PowerOffPolicy>
    </ManagedSystem>
  </content>
</entry>
"""


# ------------------------------------------------------------------ #
# Template builder tests
# ------------------------------------------------------------------ #


def test_build_managed_system_document_rename():
    xml = build_managed_system_document(new_name="prod-p9-01")
    assert "<ManagedSystem" in xml
    assert "prod-p9-01" in xml
    assert "<SystemName" in xml


def test_build_managed_system_document_power_off_policy():
    xml = build_managed_system_document(power_off_policy="autooff")
    assert "<PowerOffPolicy" in xml
    assert "autooff" in xml


def test_build_managed_system_document_lpar_start_policy():
    xml = build_managed_system_document(power_on_lpar_start_policy="autostart")
    assert "<PowerOnLparStartPolicy" in xml
    assert "autostart" in xml


def test_build_managed_system_document_mem_fields():
    xml = build_managed_system_document(
        pend_mem_region_size=256,
        requested_num_sys_huge_pages=4,
        mem_mirroring_mode="sys_firmware_only",
    )
    assert "<SystemMemoryConfiguration" in xml
    assert "256" in xml
    assert "4" in xml
    assert "sys_firmware_only" in xml


def test_build_managed_system_document_all_none_emits_metadata_only():
    xml = build_managed_system_document()
    assert "<ManagedSystem" in xml
    assert "<Metadata>" in xml
    # No optional fields should appear
    assert "<SystemName" not in xml
    assert "<PowerOffPolicy" not in xml
    assert "<SystemMemoryConfiguration" not in xml


def test_build_managed_system_document_namespace():
    xml = build_managed_system_document(new_name="x")
    assert "http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/" in xml


# ------------------------------------------------------------------ #
# Client method test
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_modify_managed_system(mock_hmc):
    route = mock_hmc.post("/rest/api/uom/ManagedSystem/sys-uuid-1").mock(
        return_value=httpx.Response(200, text=SYSTEM_ENTRY)
    )
    xml = build_managed_system_document(new_name="newsysname", power_off_policy="autooff")
    async with HMCClient(make_config()) as hmc:
        result = await hmc.modify_managed_system("sys-uuid-1", xml)
    assert route.called
    body = route.calls.last.request.content.decode()
    assert "newsysname" in body
    assert "autooff" in body
    assert result is not None
    assert result["Resource"]["SystemName"] == "newsysname"
