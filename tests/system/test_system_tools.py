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
    hmc_capacity_report,
    hmc_console_info,
    hmc_find_placement,
    hmc_find_system,
    hmc_lpars,
    hmc_list_resources,
    hmc_systems,
    hmc_vios,
)

SYSTEM_UUID = "00000000-0000-0000-0000-000000000001"
LPAR_UUID = "00000000-0000-0000-0000-000000000002"
VIOS_UUID = "00000000-0000-0000-0000-000000000003"


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
    hmc_lpars(system_name_or_uuid=SYSTEM_UUID)
    assert route.called


def test_lpars_lpar_uuid_gets_one(monkeypatch, mock_hmc):
    """hmc_lpars(lpar_uuid=...) GETs one LPAR by UUID."""
    _hmc_env(monkeypatch)
    mock_hmc.get(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}").mock(
        return_value=httpx.Response(200, text=_feed(LPAR_UUID, "LogicalPartition", PartitionState="running"))
    )
    result = hmc_lpars(lpar_name_or_uuid=LPAR_UUID)
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
    result = hmc_lpars(lpar_name_or_uuid=LPAR_UUID, state_only=True)
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
    hmc_lpars(lpar_name_or_uuid=LPAR_UUID, name="should-be-ignored")
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
    result = hmc_vios(vios_name_or_uuid=VIOS_UUID)
    assert route.called
    assert result["UUID"] == VIOS_UUID


def test_vios_uuid_takes_priority_over_system_uuid(monkeypatch, mock_hmc):
    """hmc_vios(vios_uuid=..., system_uuid=...) uses storage-detail path, ignores system_uuid."""
    _hmc_env(monkeypatch)
    route = mock_hmc.get(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}?group=ViosStorageDetail"
    ).mock(return_value=httpx.Response(200, text=_feed(VIOS_UUID, "VirtualIOServer")))
    hmc_vios(vios_name_or_uuid=VIOS_UUID, system_name_or_uuid=SYSTEM_UUID)
    assert route.called


# ---------------------------------------------------------------------- #
# hmc_find_system
# ---------------------------------------------------------------------- #


def test_find_system_found(monkeypatch, mock_hmc):
    """hmc_find_system returns the matching system entry when found."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/ManagedSystem/search/(SystemName==p9-01)").mock(
        return_value=httpx.Response(200, text=_feed(SYSTEM_UUID, "ManagedSystem", SystemName="p9-01"))
    )
    result = hmc_find_system("p9-01")
    assert result is not None
    assert result["UUID"] == SYSTEM_UUID
    assert result["Resource"]["SystemName"] == "p9-01"


def test_find_system_not_found(monkeypatch, mock_hmc):
    """hmc_find_system returns None when no system matches the name."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/ManagedSystem/search/(SystemName==ghost-sys)").mock(
        return_value=httpx.Response(200, text=EMPTY_FEED)
    )
    result = hmc_find_system("ghost-sys")
    assert result is None


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


# ---------------------------------------------------------------------- #
# hmc_capacity_report + hmc_find_placement
# ---------------------------------------------------------------------- #

SYS_UUID_A = "sys-cap-0001"
SYS_UUID_B = "sys-cap-0002"


def _sys_feed(*entries: str) -> str:
    """Wrap one or more entry XML strings in an Atom feed."""
    joined = "\n".join(entries)
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
{joined}
</feed>
"""


def _sys_entry(uuid: str, name: str, total_mem: int, total_procs: float) -> str:
    return f"""  <entry>
    <id>urn:uuid:{uuid}</id>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <ManagedSystem xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <SystemName>{name}</SystemName>
        <AssignableSystemMemory>{total_mem}</AssignableSystemMemory>
        <ConfigurableSystemProcessorUnits>{total_procs}</ConfigurableSystemProcessorUnits>
      </ManagedSystem>
    </content>
  </entry>"""


def _lpar_entry(uuid: str, name: str, mem: int, procs: float, state: str = "running") -> str:
    return f"""  <entry>
    <id>urn:uuid:{uuid}</id>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <LogicalPartition xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <PartitionName>{name}</PartitionName>
        <PartitionState>{state}</PartitionState>
        <DesiredMemory>{mem}</DesiredMemory>
        <DesiredProcessingUnits>{procs}</DesiredProcessingUnits>
      </LogicalPartition>
    </content>
  </entry>"""


def test_capacity_report_computes_per_system(monkeypatch, mock_hmc):
    """hmc_capacity_report returns total/assigned/free resources per system."""
    _hmc_env(monkeypatch)
    # Two managed systems
    mock_hmc.get("/rest/api/uom/ManagedSystem").mock(
        return_value=httpx.Response(200, text=_sys_feed(
            _sys_entry(SYS_UUID_A, "p9-01", total_mem=131072, total_procs=16.0),
            _sys_entry(SYS_UUID_B, "p9-02", total_mem=65536, total_procs=8.0),
        ))
    )
    # LPARs for system A: 2 LPARs using 16384 MiB + 2.0 procs total
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYS_UUID_A}/LogicalPartition").mock(
        return_value=httpx.Response(200, text=_sys_feed(
            _lpar_entry("lp-a1", "aix1", mem=8192, procs=1.0),
            _lpar_entry("lp-a2", "aix2", mem=8192, procs=1.0, state="not activated"),
        ))
    )
    # LPARs for system B: 1 LPAR using 4096 MiB + 0.5 procs
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYS_UUID_B}/LogicalPartition").mock(
        return_value=httpx.Response(200, text=_sys_feed(
            _lpar_entry("lp-b1", "linux1", mem=4096, procs=0.5),
        ))
    )

    result = hmc_capacity_report()

    assert len(result) == 2
    by_name = {r["system_name"]: r for r in result}

    a = by_name["p9-01"]
    assert a["total_memory_mb"] == 131072
    assert a["assigned_memory_mb"] == 16384
    assert a["free_memory_mb"] == 131072 - 16384
    assert a["total_proc_units"] == 16.0
    assert a["assigned_proc_units"] == 2.0
    assert a["free_proc_units"] == pytest.approx(14.0)
    assert a["total_lpars"] == 2
    assert a["running_lpars"] == 1  # only "running" counts

    b = by_name["p9-02"]
    assert b["assigned_memory_mb"] == 4096
    assert b["free_memory_mb"] == 65536 - 4096


def test_capacity_report_empty_lpar_list(monkeypatch, mock_hmc):
    """hmc_capacity_report handles a system with no LPARs (free == total)."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/ManagedSystem").mock(
        return_value=httpx.Response(200, text=_sys_feed(
            _sys_entry(SYS_UUID_A, "empty-sys", total_mem=65536, total_procs=8.0),
        ))
    )
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYS_UUID_A}/LogicalPartition").mock(
        return_value=httpx.Response(200, text=EMPTY_FEED)
    )

    result = hmc_capacity_report()
    assert result[0]["assigned_memory_mb"] == 0
    assert result[0]["free_memory_mb"] == 65536
    assert result[0]["running_lpars"] == 0
    assert result[0]["total_lpars"] == 0


def test_find_placement_returns_candidates(monkeypatch, mock_hmc):
    """hmc_find_placement returns systems that can host the requested LPAR."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/ManagedSystem").mock(
        return_value=httpx.Response(200, text=_sys_feed(
            _sys_entry(SYS_UUID_A, "big-sys", total_mem=131072, total_procs=16.0),
            _sys_entry(SYS_UUID_B, "small-sys", total_mem=8192, total_procs=2.0),
        ))
    )
    # big-sys: 1 LPAR using 8192 MiB / 1.0 proc → free = 122880 MiB / 15.0 procs
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYS_UUID_A}/LogicalPartition").mock(
        return_value=httpx.Response(200, text=_sys_feed(
            _lpar_entry("lp-a1", "aix1", mem=8192, procs=1.0),
        ))
    )
    # small-sys: 1 LPAR using 6144 MiB / 1.5 procs → free = 2048 MiB / 0.5 procs
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYS_UUID_B}/LogicalPartition").mock(
        return_value=httpx.Response(200, text=_sys_feed(
            _lpar_entry("lp-b1", "linux1", mem=6144, procs=1.5),
        ))
    )

    # Request 4096 MiB and 0.5 procs → only big-sys qualifies (small-sys has 2048 MiB free)
    result = hmc_find_placement(desired_memory_mb=4096, desired_proc_units=0.5)
    assert len(result) == 1
    assert result[0]["system_name"] == "big-sys"
    assert result[0]["free_memory_mb"] == 131072 - 8192


def test_find_placement_no_candidates(monkeypatch, mock_hmc):
    """hmc_find_placement returns empty list when no system has enough free resources."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/ManagedSystem").mock(
        return_value=httpx.Response(200, text=_sys_feed(
            _sys_entry(SYS_UUID_A, "full-sys", total_mem=8192, total_procs=2.0),
        ))
    )
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYS_UUID_A}/LogicalPartition").mock(
        return_value=httpx.Response(200, text=_sys_feed(
            _lpar_entry("lp-a1", "aix1", mem=8192, procs=2.0),
        ))
    )

    result = hmc_find_placement(desired_memory_mb=512)
    assert result == []


# ---------------------------------------------------------------------- #
# State filter tests — hmc_systems, hmc_lpars, hmc_vios
# ---------------------------------------------------------------------- #


def test_systems_state_filter_uses_search_endpoint(monkeypatch, mock_hmc):
    """hmc_systems(state='operating') GETs the search endpoint, not the collection."""
    _hmc_env(monkeypatch)
    route = mock_hmc.get("/rest/api/uom/ManagedSystem/search/(State==operating)").mock(
        return_value=httpx.Response(
            200, text=_feed(SYSTEM_UUID, "ManagedSystem", SystemName="s824-01", State="operating")
        )
    )
    result = hmc_systems(state="operating")
    assert route.called
    assert len(result) == 1
    assert result[0]["Resource"]["State"] == "operating"


def test_systems_state_filter_empty_returns_empty_list(monkeypatch, mock_hmc):
    """hmc_systems(state='no-match') returns [] when the search finds nothing."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/ManagedSystem/search/(State==no-match)").mock(
        return_value=httpx.Response(200, text=EMPTY_FEED)
    )
    result = hmc_systems(state="no-match")
    assert result == []


def test_lpars_state_filter_uses_search_endpoint(monkeypatch, mock_hmc):
    """hmc_lpars(state='running') GETs the PartitionState search endpoint."""
    _hmc_env(monkeypatch)
    route = mock_hmc.get(
        "/rest/api/uom/LogicalPartition/search/(PartitionState==running)"
    ).mock(
        return_value=httpx.Response(
            200, text=_feed(LPAR_UUID, "LogicalPartition", PartitionName="aix1", PartitionState="running")
        )
    )
    result = hmc_lpars(state="running")
    assert route.called
    assert len(result) == 1
    assert result[0]["Resource"]["PartitionState"] == "running"


def test_lpars_state_filter_empty_returns_empty_list(monkeypatch, mock_hmc):
    """hmc_lpars(state='not activated') returns [] when the search matches nothing."""
    _hmc_env(monkeypatch)
    mock_hmc.get(
        "/rest/api/uom/LogicalPartition/search/(PartitionState==not activated)"
    ).mock(return_value=httpx.Response(200, text=EMPTY_FEED))
    result = hmc_lpars(state="not activated")
    assert result == []


def test_lpars_state_filter_ignored_when_lpar_name_or_uuid_given(monkeypatch, mock_hmc):
    """hmc_lpars(lpar_name_or_uuid=..., state=...) resolves the UUID, ignores state."""
    _hmc_env(monkeypatch)
    route = mock_hmc.get(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}").mock(
        return_value=httpx.Response(
            200, text=_feed(LPAR_UUID, "LogicalPartition", PartitionState="running")
        )
    )
    result = hmc_lpars(lpar_name_or_uuid=LPAR_UUID, state="running")
    assert route.called
    assert result["UUID"] == LPAR_UUID


def test_vios_state_filter_uses_search_endpoint(monkeypatch, mock_hmc):
    """hmc_vios(state='running') GETs the VirtualIOServer PartitionState search endpoint."""
    _hmc_env(monkeypatch)
    route = mock_hmc.get(
        "/rest/api/uom/VirtualIOServer/search/(PartitionState==running)"
    ).mock(
        return_value=httpx.Response(
            200, text=_feed(VIOS_UUID, "VirtualIOServer", PartitionName="vios1", PartitionState="running")
        )
    )
    result = hmc_vios(state="running")
    assert route.called
    assert len(result) == 1
    assert result[0]["Resource"]["PartitionState"] == "running"


def test_vios_state_filter_empty_returns_empty_list(monkeypatch, mock_hmc):
    """hmc_vios(state='no-match') returns [] when the search matches nothing."""
    _hmc_env(monkeypatch)
    mock_hmc.get(
        "/rest/api/uom/VirtualIOServer/search/(PartitionState==no-match)"
    ).mock(return_value=httpx.Response(200, text=EMPTY_FEED))
    result = hmc_vios(state="no-match")
    assert result == []


def test_vios_state_filter_ignored_when_vios_name_or_uuid_given(monkeypatch, mock_hmc):
    """hmc_vios(vios_name_or_uuid=..., state=...) returns storage detail, ignores state."""
    _hmc_env(monkeypatch)
    route = mock_hmc.get(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}?group=ViosStorageDetail"
    ).mock(
        return_value=httpx.Response(
            200, text=_feed(VIOS_UUID, "VirtualIOServer", PartitionName="vios1")
        )
    )
    hmc_vios(vios_name_or_uuid=VIOS_UUID, state="running")
    assert route.called

