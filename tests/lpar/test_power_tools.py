"""Tool-layer tests for the DLPAR and system/VIOS power MCP tools.

The document builders and client methods are covered in test_dlpar.py and
test_power.py; these tests call the actual ``@mcp.tool`` functions in
``server_lpars`` against the respx ``mock_hmc`` router so the
argument->URL and argument->XML mapping in the tool bodies is exercised.
LPAR create/modify and LPAR power-on/off are covered in
``tests/app/test_server_tools.py``; the delete-LPAR precondition guard is
covered in ``tests/app/test_capabilities.py``.
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from hmc_mcp.client import HMCError
from hmc_mcp.documents import LparResources
from hmc_mcp.server import (
    hmc_dlpar_mem,
    hmc_dlpar_proc,
    hmc_modify_system,
    hmc_power_off_system,
    hmc_power_off_vios,
    hmc_power_on_system,
    hmc_power_on_vios,
)
from conftest import JOB_ENTRY, SYSTEM_ENTRY


def test_power_off_rejects_invalid_wait_timing_before_client_creation(monkeypatch):
    called = False

    def unexpected_client(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("client must not be created")

    monkeypatch.setattr("hmc_mcp.server_systems.client_from_env", unexpected_client)
    with pytest.raises(ValueError, match="timeout_seconds"):
        hmc_power_off_system(SYSTEM_UUID, wait=True, timeout_seconds=-1)
    assert called is False


SYSTEM_UUID = "00000000-0000-0000-0000-000000000001"
VIOS_UUID = "00000000-0000-0000-0000-000000000003"
LPAR_UUID = "00000000-0000-0000-0000-000000000002"

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


def _hmc_env(monkeypatch) -> None:
    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "abc123")


def _mock_dlpar_authorization(router) -> None:
    """The system/partition reads ADR 0092's guard makes before a DLPAR write."""
    router.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}").mock(
        return_value=httpx.Response(
            200, text=SYSTEM_ENTRY.format(uuid=SYSTEM_UUID, name="sys1")
        )
    )
    router.get(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}").mock(
        return_value=httpx.Response(200, text=LPAR_ENTRY)
    )


def _unowned_partition():
    """Patch the SSH ownership read to report a partition with no ADR 0011 stamp."""
    return patch(
        "hmc_mcp.operations_lpar.get_lpar_description",
        new=AsyncMock(return_value=""),
    )


# ---------------------------------------------------------------------- #
# DLPAR
# ---------------------------------------------------------------------- #


def test_dlpar_proc_posts_proc_document(monkeypatch, mock_hmc):
    """hmc_dlpar_proc POSTs a PartitionProcessorConfiguration document."""
    _hmc_env(monkeypatch)
    _mock_dlpar_authorization(mock_hmc)
    route = mock_hmc.post(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}").mock(
        return_value=httpx.Response(200, text=LPAR_ENTRY)
    )
    with _unowned_partition():
        result = hmc_dlpar_proc(
            LPAR_UUID,
            LparResources(desired_procs=1.5, desired_vcpus=3),
            system_name_or_uuid=SYSTEM_UUID,
        )
    body = route.calls.last.request.content.decode()
    assert "PartitionProcessorConfiguration" in body
    assert "DesiredProcessingUnits" in body and ">1.5<" in body
    assert "DesiredVirtualProcessors" in body and ">3<" in body
    assert "PartitionMemoryConfiguration" not in body
    assert result["Resource"]["PartitionState"] == "running"


def test_dlpar_mem_posts_mem_document(monkeypatch, mock_hmc):
    """hmc_dlpar_mem POSTs a PartitionMemoryConfiguration document."""
    _hmc_env(monkeypatch)
    _mock_dlpar_authorization(mock_hmc)
    route = mock_hmc.post(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}").mock(
        return_value=httpx.Response(200, text=LPAR_ENTRY)
    )
    with _unowned_partition():
        result = hmc_dlpar_mem(
            LPAR_UUID,
            LparResources(desired_memory=8192, min_memory=1024, max_memory=16384),
            system_name_or_uuid=SYSTEM_UUID,
        )
    body = route.calls.last.request.content.decode()
    assert "PartitionMemoryConfiguration" in body
    assert "DesiredMemory" in body and ">8192<" in body
    assert "MinimumMemory" in body and ">1024<" in body
    assert "MaximumMemory" in body and ">16384<" in body
    assert "PartitionProcessorConfiguration" not in body
    assert result["Resource"]["PartitionName"] == "lpar1"


def test_dlpar_proc_error_propagates(monkeypatch, mock_hmc):
    """A non-2xx DLPAR POST surfaces as HMCError."""
    _hmc_env(monkeypatch)
    _mock_dlpar_authorization(mock_hmc)
    mock_hmc.post(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}").mock(
        return_value=httpx.Response(500, text="<error>boom</error>")
    )
    with _unowned_partition(), pytest.raises(HMCError) as exc_info:
        hmc_dlpar_proc(
            LPAR_UUID,
            LparResources(desired_procs=1.0),
            system_name_or_uuid=SYSTEM_UUID,
        )
    assert exc_info.value.status_code == 500


def test_dlpar_proc_refuses_a_foreign_owned_partition(monkeypatch, mock_hmc):
    """ADR 0092 §3.2: the tool reaches the guard, not just the operation."""
    _hmc_env(monkeypatch)
    monkeypatch.setenv("HMC_AGENT_ID", "alice")
    _mock_dlpar_authorization(mock_hmc)
    route = mock_hmc.post(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}").mock(
        return_value=httpx.Response(200, text=LPAR_ENTRY)
    )
    with patch(
        "hmc_mcp.operations_lpar.get_lpar_description",
        new=AsyncMock(return_value="[hmc-mcp owner:bob created:2026-08-14]"),
    ):
        with pytest.raises(PermissionError, match="ownership_override=true"):
            hmc_dlpar_proc(
                LPAR_UUID,
                LparResources(desired_procs=1.0),
                system_name_or_uuid=SYSTEM_UUID,
            )
    assert not route.called


def test_dlpar_mem_ownership_override_reaches_the_write(monkeypatch, mock_hmc):
    """The tool threads ADR 0092 §5's per-call override into the operation."""
    _hmc_env(monkeypatch)
    monkeypatch.setenv("HMC_AGENT_ID", "alice")
    _mock_dlpar_authorization(mock_hmc)
    route = mock_hmc.post(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}").mock(
        return_value=httpx.Response(200, text=LPAR_ENTRY)
    )
    read = AsyncMock(return_value="[hmc-mcp owner:bob created:2026-08-14]")
    with patch("hmc_mcp.operations_lpar.get_lpar_description", new=read):
        hmc_dlpar_mem(
            LPAR_UUID,
            LparResources(desired_memory=4096),
            system_name_or_uuid=SYSTEM_UUID,
            ownership_override=True,
        )
    read.assert_not_awaited()
    assert route.called


# ---------------------------------------------------------------------- #
# Managed-system / VIOS power
# ---------------------------------------------------------------------- #


def test_power_on_system_submits_job(monkeypatch, mock_hmc):
    """hmc_power_on_system PUTs a PowerOn job to the ManagedSystem."""
    _hmc_env(monkeypatch)
    route = mock_hmc.put(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/do/PowerOn").mock(
        return_value=httpx.Response(202, text=JOB_ENTRY)
    )
    result = hmc_power_on_system(SYSTEM_UUID)
    body = route.calls.last.request.content.decode()
    assert "PowerOn</OperationName>" in body and "ManagedSystem" in body
    assert result["Resource"]["JobID"] == "job-uuid-999"


def test_power_off_system_submits_job(monkeypatch, mock_hmc):
    """hmc_power_off_system PUTs a PowerOff job with the immediate flag."""
    _hmc_env(monkeypatch)
    route = mock_hmc.put(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/do/PowerOff").mock(
        return_value=httpx.Response(202, text=JOB_ENTRY)
    )
    hmc_power_off_system(SYSTEM_UUID, immediate=True)
    body = route.calls.last.request.content.decode()
    assert "PowerOff</OperationName>" in body
    assert '<ParameterName kb="ROR" kxe="false">immediate</ParameterName>' in body
    assert '<ParameterValue kb="CUR" kxe="false">true</ParameterValue>' in body


def test_power_on_vios_submits_job(monkeypatch, mock_hmc):
    """hmc_power_on_vios PUTs a PowerOn job to the VirtualIOServer."""
    _hmc_env(monkeypatch)
    route = mock_hmc.put(f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/do/PowerOn").mock(
        return_value=httpx.Response(202, text=JOB_ENTRY)
    )
    result = hmc_power_on_vios(VIOS_UUID)
    body = route.calls.last.request.content.decode()
    assert "PowerOn</OperationName>" in body and "VirtualIOServer" in body
    assert result["Resource"]["JobID"] == "job-uuid-999"


def test_power_off_vios_submits_job(monkeypatch, mock_hmc):
    """hmc_power_off_vios PUTs a PowerOff job, graceful by default."""
    _hmc_env(monkeypatch)
    route = mock_hmc.put(f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/do/PowerOff").mock(
        return_value=httpx.Response(202, text=JOB_ENTRY)
    )
    hmc_power_off_vios(VIOS_UUID)
    body = route.calls.last.request.content.decode()
    assert "PowerOff</OperationName>" in body
    # Graceful shutdown omits the immediate parameter entirely.
    assert "immediate" not in body


def test_modify_system_builds_xml(monkeypatch, mock_hmc):
    """hmc_modify_system maps its args into the ManagedSystem document."""
    _hmc_env(monkeypatch)
    route = mock_hmc.post(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}").mock(
        return_value=httpx.Response(
            200, text=SYSTEM_ENTRY.format(uuid=SYSTEM_UUID, name="newsysname")
        )
    )
    result = hmc_modify_system(
        SYSTEM_UUID,
        new_name="newsysname",
        power_off_policy=1,
        power_on_lpar_start_policy="autostart",
        mem_mirroring_mode="sys_firmware_only",
    )
    body = route.calls.last.request.content.decode()
    assert "newsysname</SystemName>" in body
    assert '<PowerOffPolicy kb="CUD" kxe="false">1</PowerOffPolicy>' in body
    assert (
        '<PowerOnLparStartPolicy kb="CUD" kxe="false">autostart</PowerOnLparStartPolicy>'
        in body
    )
    assert (
        '<MemoryMirroringMode kb="CUD" kxe="false">sys_firmware_only</MemoryMirroringMode>'
        in body
    )
    assert result["Resource"]["SystemName"] == "newsysname"


def test_modify_system_invalid_policy_rejected_before_http(monkeypatch, mock_hmc):
    """An invalid power_off_policy raises ValueError before any HTTP call."""
    _hmc_env(monkeypatch)
    route = mock_hmc.post(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}")
    with pytest.raises(ValueError, match="power_off_policy"):
        hmc_modify_system(SYSTEM_UUID, power_off_policy=2)
    assert not route.called
