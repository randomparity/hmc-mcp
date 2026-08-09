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
