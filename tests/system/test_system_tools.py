"""Tool-layer tests for the read-only inventory MCP tools.

The client methods are covered in the domain test dirs; these tests call the
actual ``@mcp.tool`` functions in ``server_system`` against the respx
``mock_hmc`` router so the argument->URL mapping in the tool bodies is
exercised. hmc_get_job / hmc_find_lpar are covered in
``tests/app/test_server_tools.py``; hmc_run_command (SSH) is covered there too.
"""

import httpx
import pytest

from hmc_mcp.client import HMCError
from hmc_mcp.server import (
    hmc_console_info,
    hmc_get_lpar,
    hmc_get_system,
    hmc_lpar_state,
    hmc_list_lpars,
    hmc_list_resources,
    hmc_list_systems,
    hmc_list_vios,
    hmc_vios_mappings,
)

SYSTEM_UUID = "sys-uuid-0001"
LPAR_UUID = "lpar-uuid-0001"
VIOS_UUID = "vios-uuid-0001"


def _hmc_env(monkeypatch) -> None:
    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "abc123")


def _feed(uuid: str, rtype: str, **fields: str) -> str:
    """A single-resource Atom feed; {fields} render as resource elements."""
    body = "\n".join(
        f"        <{name} xmlns=\"http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/\">{value}</{name}>"
        for name, value in fields.items()
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:{uuid}</id>
    <title>{rtype}:{uuid}</title>
    <link rel="SELF" href="https://hmc.test:12443/rest/api/uom/{rtype}/{uuid}"/>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <{rtype} xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
{body}
      </{rtype}>
    </content>
  </entry>
</feed>
"""


# ---------------------------------------------------------------------- #
# Console / systems
# ---------------------------------------------------------------------- #


def test_console_info_returns_management_console(monkeypatch, mock_hmc):
    """hmc_console_info GETs the ManagementConsole collection."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/ManagementConsole").mock(
        return_value=httpx.Response(
            200,
            text=_feed("mc-uuid-1", "ManagementConsole", Version="V10R1M1040"),
        )
    )
    result = hmc_console_info()
    assert result["UUID"] == "mc-uuid-1"
    assert result["Resource"]["Version"] == "V10R1M1040"


def test_list_systems(monkeypatch, mock_hmc):
    """hmc_list_systems GETs the ManagedSystem collection."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/ManagedSystem").mock(
        return_value=httpx.Response(200, text=_feed(SYSTEM_UUID, "ManagedSystem", SystemName="s824-01"))
    )
    result = hmc_list_systems()
    assert result[0]["UUID"] == SYSTEM_UUID
    assert result[0]["Resource"]["SystemName"] == "s824-01"


def test_get_system(monkeypatch, mock_hmc):
    """hmc_get_system GETs one managed system by UUID."""
    _hmc_env(monkeypatch)
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}").mock(
        return_value=httpx.Response(200, text=_feed(SYSTEM_UUID, "ManagedSystem", State="operating"))
    )
    result = hmc_get_system(SYSTEM_UUID)
    assert result["Resource"]["State"] == "operating"


def test_get_system_error_propagates(monkeypatch, mock_hmc):
    """A 404 on hmc_get_system surfaces as HMCError with the status code."""
    _hmc_env(monkeypatch)
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}").mock(
        return_value=httpx.Response(404, text="<error>not found</error>")
    )
    with pytest.raises(HMCError) as exc_info:
        hmc_get_system(SYSTEM_UUID)
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------- #
# LPARs
# ---------------------------------------------------------------------- #


def test_list_lpars_all(monkeypatch, mock_hmc):
    """hmc_list_lpars with no system_uuid GETs the global LogicalPartition feed."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/LogicalPartition").mock(
        return_value=httpx.Response(200, text=_feed(LPAR_UUID, "LogicalPartition", PartitionName="aix1"))
    )
    result = hmc_list_lpars()
    assert result[0]["Resource"]["PartitionName"] == "aix1"


def test_list_lpars_scoped(monkeypatch, mock_hmc):
    """hmc_list_lpars with system_uuid GETs the system-scoped child feed."""
    _hmc_env(monkeypatch)
    route = mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition").mock(
        return_value=httpx.Response(200, text=_feed(LPAR_UUID, "LogicalPartition", PartitionName="aix1"))
    )
    hmc_list_lpars(SYSTEM_UUID)
    assert route.called


def test_get_lpar(monkeypatch, mock_hmc):
    """hmc_get_lpar GETs one LPAR by UUID."""
    _hmc_env(monkeypatch)
    mock_hmc.get(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}").mock(
        return_value=httpx.Response(200, text=_feed(LPAR_UUID, "LogicalPartition", PartitionState="running"))
    )
    result = hmc_get_lpar(LPAR_UUID)
    assert result["Resource"]["PartitionState"] == "running"


def test_lpar_state_uses_quick_property(monkeypatch, mock_hmc):
    """hmc_lpar_state GETs the cheap quick/PartitionState endpoint."""
    _hmc_env(monkeypatch)
    route = mock_hmc.get(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/quick/PartitionState"
    ).mock(return_value=httpx.Response(200, text="running"))
    result = hmc_lpar_state(LPAR_UUID)
    assert route.called
    assert result == "running"


# ---------------------------------------------------------------------- #
# VIOS / generic resources
# ---------------------------------------------------------------------- #


def test_list_vios_all(monkeypatch, mock_hmc):
    """hmc_list_vios with no system_uuid GETs the VirtualIOServer feed."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/VirtualIOServer").mock(
        return_value=httpx.Response(200, text=_feed(VIOS_UUID, "VirtualIOServer", PartitionName="vios1"))
    )
    result = hmc_list_vios()
    assert result[0]["Resource"]["PartitionName"] == "vios1"


def test_vios_mappings(monkeypatch, mock_hmc):
    """hmc_vios_mappings GETs the ViosStorageDetail group."""
    _hmc_env(monkeypatch)
    route = mock_hmc.get(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}?group=ViosStorageDetail"
    ).mock(return_value=httpx.Response(200, text=_feed(VIOS_UUID, "VirtualIOServer", PartitionName="vios1")))
    result = hmc_vios_mappings(VIOS_UUID)
    assert route.called
    assert result["UUID"] == VIOS_UUID


def test_list_resources(monkeypatch, mock_hmc):
    """hmc_list_resources GETs the requested resource type collection."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/Cluster").mock(
        return_value=httpx.Response(200, text=_feed("cluster-uuid-1", "Cluster"))
    )
    result = hmc_list_resources("Cluster")
    assert result[0]["UUID"] == "cluster-uuid-1"


# ---------------------------------------------------------------------- #
# hmc_capacity_report / hmc_find_placement
# ---------------------------------------------------------------------- #

from hmc_mcp.server import hmc_capacity_report, hmc_find_placement  # noqa: E402


def _system_feed_with_mem(uuid: str, name: str, mem_mb: int, proc_units_x100: int) -> str:
    """A ManagedSystem feed entry with capacity fields."""
    body = (
        f'        <SystemName xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">{name}</SystemName>\n'
        f'        <State xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">operating</State>\n'
        f'        <InstalledSystemMemory xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">{mem_mb}</InstalledSystemMemory>\n'
        f'        <InstalledSystemProcessorUnits xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">{proc_units_x100}</InstalledSystemProcessorUnits>\n'
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:{uuid}</id>
    <title>ManagedSystem:{name}</title>
    <link rel="SELF" href="https://hmc.test:12443/rest/api/uom/ManagedSystem/{uuid}"/>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <ManagedSystem xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
{body}
      </ManagedSystem>
    </content>
  </entry>
</feed>
"""


def _lpar_feed_with_resources(uuid: str, name: str, mem_mb: int, procs: float, state: str = "running") -> str:
    """An LPAR feed entry with DesiredMemory and DesiredProcessingUnits."""
    body = (
        f'        <PartitionName xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">{name}</PartitionName>\n'
        f'        <PartitionState xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">{state}</PartitionState>\n'
        f'        <DesiredMemory xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">{mem_mb}</DesiredMemory>\n'
        f'        <DesiredProcessingUnits xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">{procs}</DesiredProcessingUnits>\n'
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:{uuid}</id>
    <title>LogicalPartition:{name}</title>
    <link rel="SELF" href="https://hmc.test:12443/rest/api/uom/LogicalPartition/{uuid}"/>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <LogicalPartition xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
{body}
      </LogicalPartition>
    </content>
  </entry>
</feed>
"""


def test_capacity_report_single_system(monkeypatch, mock_hmc):
    """hmc_capacity_report aggregates system and LPAR data correctly."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/ManagedSystem").mock(
        return_value=httpx.Response(200, text=_system_feed_with_mem(SYSTEM_UUID, "s824-01", 131072, 800))
    )
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition").mock(
        return_value=httpx.Response(
            200, text=_lpar_feed_with_resources(LPAR_UUID, "web01", 8192, 0.5, "running")
        )
    )
    result = hmc_capacity_report()
    assert len(result) == 1
    row = result[0]
    assert row["system_name"] == "s824-01"
    assert row["total_memory_mb"] == 131072
    assert row["assigned_memory_mb"] == 8192
    assert row["free_memory_mb"] == 131072 - 8192
    assert row["running_lpars"] == 1
    assert row["total_lpars"] == 1


def test_capacity_report_empty(monkeypatch, mock_hmc):
    """hmc_capacity_report returns an empty list when no systems exist."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/ManagedSystem").mock(
        return_value=httpx.Response(200, text='<?xml version="1.0" encoding="UTF-8" standalone="yes"?><feed xmlns="http://www.w3.org/2005/Atom"/>')
    )
    result = hmc_capacity_report()
    assert result == []


def test_find_placement_returns_candidates(monkeypatch, mock_hmc):
    """hmc_find_placement returns systems meeting the memory+proc threshold."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/ManagedSystem").mock(
        return_value=httpx.Response(200, text=_system_feed_with_mem(SYSTEM_UUID, "s824-01", 131072, 800))
    )
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition").mock(
        return_value=httpx.Response(
            200, text=_lpar_feed_with_resources(LPAR_UUID, "web01", 8192, 0.5, "running")
        )
    )
    candidates = hmc_find_placement(desired_memory_mb=4096, desired_proc_units=0.5)
    assert len(candidates) == 1
    assert candidates[0]["system_name"] == "s824-01"


def test_find_placement_no_candidates(monkeypatch, mock_hmc):
    """hmc_find_placement returns empty list when no system fits."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/ManagedSystem").mock(
        return_value=httpx.Response(200, text=_system_feed_with_mem(SYSTEM_UUID, "tiny-sys", 4096, 100))
    )
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition").mock(
        return_value=httpx.Response(
            200, text='<?xml version="1.0" encoding="UTF-8" standalone="yes"?><feed xmlns="http://www.w3.org/2005/Atom"/>'
        )
    )
    candidates = hmc_find_placement(desired_memory_mb=8192, desired_proc_units=0.5)
    assert candidates == []
