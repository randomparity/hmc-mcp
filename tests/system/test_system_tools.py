"""Tool-layer tests for the consolidated read-only inventory MCP tools.

The client methods are covered in the domain test dirs; these tests call the
actual ``@mcp.tool`` functions in ``server_system`` against the respx
``mock_hmc`` router so the argument->URL mapping in the tool bodies is
exercised. hmc_get_job is covered in ``tests/app/test_server_tools.py``;
hmc_run_command (SSH) is covered there too.
"""

import httpx
import pytest

from hmc_mcp.client import HMCError
from hmc_mcp.server import (
    hmc_console_info,
    hmc_lpars,
    hmc_list_resources,
    hmc_systems,
    hmc_vios,
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

LPAR_SEARCH_FEED = """\
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:{uuid}</id>
    <title>LogicalPartition:{name}</title>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <LogicalPartition xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <PartitionName>{name}</PartitionName>
      </LogicalPartition>
    </content>
  </entry>
</feed>
"""

EMPTY_FEED = """\
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom"/>
"""


# ---------------------------------------------------------------------- #
# Console
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


# ---------------------------------------------------------------------- #
# hmc_systems
# ---------------------------------------------------------------------- #


def test_systems_no_arg_lists_all(monkeypatch, mock_hmc):
    """hmc_systems() lists all managed systems."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/ManagedSystem").mock(
        return_value=httpx.Response(200, text=_feed(SYSTEM_UUID, "ManagedSystem", SystemName="s824-01"))
    )
    result = hmc_systems()
    assert result[0]["UUID"] == SYSTEM_UUID
    assert result[0]["Resource"]["SystemName"] == "s824-01"


def test_systems_with_uuid_gets_one(monkeypatch, mock_hmc):
    """hmc_systems(system_uuid=UUID) returns one system dict."""
    _hmc_env(monkeypatch)
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}").mock(
        return_value=httpx.Response(200, text=_feed(SYSTEM_UUID, "ManagedSystem", State="operating"))
    )
    result = hmc_systems(SYSTEM_UUID)
    assert result["Resource"]["State"] == "operating"


def test_systems_with_uuid_404_propagates(monkeypatch, mock_hmc):
    """A 404 on hmc_systems(uuid) surfaces as HMCError with the status code."""
    _hmc_env(monkeypatch)
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}").mock(
        return_value=httpx.Response(404, text="<error>not found</error>")
    )
    with pytest.raises(HMCError) as exc_info:
        hmc_systems(SYSTEM_UUID)
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------- #
# hmc_lpars
# ---------------------------------------------------------------------- #


def test_lpars_no_arg_lists_all(monkeypatch, mock_hmc):
    """hmc_lpars() GETs the global LogicalPartition feed."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/LogicalPartition").mock(
        return_value=httpx.Response(200, text=_feed(LPAR_UUID, "LogicalPartition", PartitionName="aix1"))
    )
    result = hmc_lpars()
    assert result[0]["Resource"]["PartitionName"] == "aix1"


def test_lpars_system_uuid_scopes(monkeypatch, mock_hmc):
    """hmc_lpars(system_uuid=...) uses the system-scoped child feed URL."""
    _hmc_env(monkeypatch)
    route = mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition").mock(
        return_value=httpx.Response(200, text=_feed(LPAR_UUID, "LogicalPartition", PartitionName="aix1"))
    )
    hmc_lpars(system_uuid=SYSTEM_UUID)
    assert route.called


def test_lpars_lpar_uuid_gets_one(monkeypatch, mock_hmc):
    """hmc_lpars(lpar_uuid=...) GETs one LPAR by UUID."""
    _hmc_env(monkeypatch)
    mock_hmc.get(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}").mock(
        return_value=httpx.Response(200, text=_feed(LPAR_UUID, "LogicalPartition", PartitionState="running"))
    )
    result = hmc_lpars(lpar_uuid=LPAR_UUID)
    assert result["Resource"]["PartitionState"] == "running"


def test_lpars_name_finds_by_name(monkeypatch, mock_hmc):
    """hmc_lpars(name=...) searches by PartitionName and returns the entry."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/LogicalPartition/search/(PartitionName==aixprod)").mock(
        return_value=httpx.Response(200, text=LPAR_SEARCH_FEED.format(uuid=LPAR_UUID, name="aixprod"))
    )
    result = hmc_lpars(name="aixprod")
    assert result["UUID"] == LPAR_UUID
    assert result["Resource"]["PartitionName"] == "aixprod"


def test_lpars_name_not_found_returns_none(monkeypatch, mock_hmc):
    """hmc_lpars(name=...) returns None when the search matches nothing."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/LogicalPartition/search/(PartitionName==ghost)").mock(
        return_value=httpx.Response(200, text=EMPTY_FEED)
    )
    assert hmc_lpars(name="ghost") is None


def test_lpars_state_only_returns_string(monkeypatch, mock_hmc):
    """hmc_lpars(lpar_uuid=..., state_only=True) uses the cheap quick-property endpoint."""
    _hmc_env(monkeypatch)
    route = mock_hmc.get(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/quick/PartitionState"
    ).mock(return_value=httpx.Response(200, text="running"))
    result = hmc_lpars(lpar_uuid=LPAR_UUID, state_only=True)
    assert route.called
    assert result == "running"


def test_lpars_state_only_without_lpar_uuid_raises():
    """hmc_lpars(state_only=True) without lpar_uuid raises ValueError."""
    with pytest.raises(ValueError, match="state_only"):
        hmc_lpars(state_only=True)


def test_lpars_lpar_uuid_takes_priority_over_name(monkeypatch, mock_hmc):
    """hmc_lpars(lpar_uuid=..., name=...) resolves lpar_uuid, ignores name."""
    _hmc_env(monkeypatch)
    route = mock_hmc.get(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}").mock(
        return_value=httpx.Response(200, text=_feed(LPAR_UUID, "LogicalPartition", PartitionName="aix1"))
    )
    hmc_lpars(lpar_uuid=LPAR_UUID, name="should-be-ignored")
    assert route.called


# ---------------------------------------------------------------------- #
# hmc_vios
# ---------------------------------------------------------------------- #


def test_vios_no_arg_lists_all(monkeypatch, mock_hmc):
    """hmc_vios() GETs the VirtualIOServer feed."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/VirtualIOServer").mock(
        return_value=httpx.Response(200, text=_feed(VIOS_UUID, "VirtualIOServer", PartitionName="vios1"))
    )
    result = hmc_vios()
    assert result[0]["Resource"]["PartitionName"] == "vios1"


def test_vios_with_uuid_returns_storage_detail(monkeypatch, mock_hmc):
    """hmc_vios(vios_uuid=...) GETs the ViosStorageDetail group."""
    _hmc_env(monkeypatch)
    route = mock_hmc.get(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}?group=ViosStorageDetail"
    ).mock(return_value=httpx.Response(200, text=_feed(VIOS_UUID, "VirtualIOServer", PartitionName="vios1")))
    result = hmc_vios(vios_uuid=VIOS_UUID)
    assert route.called
    assert result["UUID"] == VIOS_UUID


def test_vios_uuid_takes_priority_over_system_uuid(monkeypatch, mock_hmc):
    """hmc_vios(vios_uuid=..., system_uuid=...) uses storage-detail path, ignores system_uuid."""
    _hmc_env(monkeypatch)
    route = mock_hmc.get(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}?group=ViosStorageDetail"
    ).mock(return_value=httpx.Response(200, text=_feed(VIOS_UUID, "VirtualIOServer")))
    hmc_vios(vios_uuid=VIOS_UUID, system_uuid=SYSTEM_UUID)
    assert route.called


# ---------------------------------------------------------------------- #
# hmc_list_resources (unchanged)
# ---------------------------------------------------------------------- #


def test_list_resources(monkeypatch, mock_hmc):
    """hmc_list_resources GETs the requested resource type collection."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/Cluster").mock(
        return_value=httpx.Response(200, text=_feed("cluster-uuid-1", "Cluster"))
    )
    result = hmc_list_resources("Cluster")
    assert result[0]["UUID"] == "cluster-uuid-1"
