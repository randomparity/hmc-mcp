"""Tests for the hmc_system_summary composite tool.

Exercises the tool against a mocked HMC (respx) so the URL mapping and
field-extraction logic in server_tools/composite.py is verified without a live HMC.
"""

from __future__ import annotations

import httpx
import pytest
from conftest import captured_lpar_entry, captured_system_entry, live_fixture

from hmcpctl.server_tools.inventory.composite import hmc_system_summary

SYSTEM_UUID = "00000000-0000-0000-0000-000000000001"
LPAR_UUID_1 = "00000000-0000-0000-0000-000000000002"
LPAR_UUID_2 = "00000000-0000-0000-0000-000000000003"
VIOS_UUID_1 = "00000000-0000-0000-0000-000000000004"
VIOS_UUID_2 = "00000000-0000-0000-0000-000000000005"

NS = "http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/"


def _hmc_env(monkeypatch) -> None:
    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "abc123")


def _system_feed(uuid: str, name: str, **kwargs) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
{captured_system_entry(uuid, name, **kwargs)}
</feed>
"""


def _lpar_feed(*entries: tuple[str, str, str]) -> str:
    """entries: (uuid, name, state) tuples in the captured partition shape."""
    joined = "\n".join(captured_lpar_entry(*entry) for entry in entries)
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
{joined}
</feed>
"""


def _vios_feed(*uuids: str) -> str:
    parts = []
    for uuid in uuids:
        parts.append(f"""  <entry>
    <id>urn:uuid:{uuid}</id>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <VirtualIOServer xmlns="{NS}">
        <PartitionName>vios-{uuid[-4:]}</PartitionName>
      </VirtualIOServer>
    </content>
  </entry>""")
    joined = "\n".join(parts)
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
{joined}
</feed>
"""


EMPTY_FEED = """\
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom"/>
"""


# ---------------------------------------------------------------------- #
# Core happy-path
# ---------------------------------------------------------------------- #


def test_system_summary_by_uuid_returns_flat_dict(monkeypatch, mock_hmc):
    """hmc_system_summary(uuid) fetches system + LPARs + VIOS and returns summary."""
    _hmc_env(monkeypatch)
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}").mock(
        return_value=httpx.Response(
            200,
            text=_system_feed(SYSTEM_UUID, "p9-prod"),
        )
    )
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition").mock(
        return_value=httpx.Response(
            200,
            text=_lpar_feed(
                (LPAR_UUID_1, "aix-prod", "running"),
                (LPAR_UUID_2, "linux-web", "not activated"),
            ),
        )
    )
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/VirtualIOServer").mock(
        return_value=httpx.Response(200, text=_vios_feed(VIOS_UUID_1, VIOS_UUID_2))
    )

    result = hmc_system_summary(SYSTEM_UUID)

    assert result.uuid == SYSTEM_UUID
    assert result.name == "p9-prod"
    assert result.state == "operating"
    assert result.mtms == "8375-42A*SERIAL0"
    assert result.firmware_version == "VL950_FW950.00 (39)"
    assert result.total_memory_mib == 131072
    assert result.free_memory_mib == 112448
    assert result.total_proc_units == 20.0
    assert result.free_proc_units == 18.0
    assert result.lpar_count == 2
    assert result.lpar_states == {"running": 1, "not activated": 1}
    assert result.vios_count == 2


def test_system_summary_no_lpars_no_vios(monkeypatch, mock_hmc):
    """hmc_system_summary with empty LPAR + VIOS feeds returns zero counts."""
    _hmc_env(monkeypatch)
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}").mock(
        return_value=httpx.Response(
            200,
            text=_system_feed(
                SYSTEM_UUID,
                "empty-sys",
                configurable_mem="65536",
                available_mem="65536",
                configurable_proc="8",
                available_proc="8",
            ),
        )
    )
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition").mock(
        return_value=httpx.Response(200, text=EMPTY_FEED)
    )
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/VirtualIOServer").mock(
        return_value=httpx.Response(200, text=EMPTY_FEED)
    )

    result = hmc_system_summary(SYSTEM_UUID)

    assert result.lpar_count == 0
    assert result.lpar_states == {}
    assert result.vios_count == 0
    assert result.free_memory_mib == 65536
    assert result.free_proc_units == pytest.approx(8.0)


def test_system_summary_by_name_resolves_uuid(monkeypatch, mock_hmc):
    """hmc_system_summary('myname') resolves via search then fetches summary."""
    _hmc_env(monkeypatch)
    search_feed = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:{SYSTEM_UUID}</id>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <ManagedSystem xmlns="{NS}">
        <SystemName>p9-prod</SystemName>
      </ManagedSystem>
    </content>
  </entry>
</feed>
"""
    mock_hmc.get("/rest/api/uom/ManagedSystem/search/(SystemName==p9-prod)").mock(
        return_value=httpx.Response(200, text=search_feed)
    )
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}").mock(
        return_value=httpx.Response(
            200,
            text=_system_feed(SYSTEM_UUID, "p9-prod"),
        )
    )
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition").mock(
        return_value=httpx.Response(200, text=EMPTY_FEED)
    )
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/VirtualIOServer").mock(
        return_value=httpx.Response(200, text=EMPTY_FEED)
    )

    result = hmc_system_summary("p9-prod")

    assert result.name == "p9-prod"
    assert result.uuid == SYSTEM_UUID
    assert result.state == "operating"


def test_system_summary_name_not_found_raises(monkeypatch, mock_hmc):
    """hmc_system_summary raises ValueError when the system name is unknown."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/ManagedSystem/search/(SystemName==ghost-sys)").mock(
        return_value=httpx.Response(200, text=EMPTY_FEED)
    )

    with pytest.raises(ValueError, match="ghost-sys"):
        hmc_system_summary("ghost-sys")


def _mock_bare_system(mock_hmc, *omit: str) -> None:
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}").mock(
        return_value=httpx.Response(
            200, text=_system_feed(SYSTEM_UUID, "bare-sys", omit=omit)
        )
    )
    for child in ("LogicalPartition", "VirtualIOServer"):
        mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/{child}").mock(
            return_value=httpx.Response(200, text=EMPTY_FEED)
        )


def test_system_summary_missing_optional_fields(monkeypatch, mock_hmc):
    """A system without MTMS or firmware reports them as unknown, not as a mapping."""
    _hmc_env(monkeypatch)
    _mock_bare_system(mock_hmc, "MachineTypeModelAndSerialNumber", "SystemFirmware")

    result = hmc_system_summary(SYSTEM_UUID)

    assert result.mtms is None
    assert result.firmware_version is None
    assert result.total_memory_mib == 131072


def test_system_summary_missing_capacity_fails(monkeypatch, mock_hmc):
    """A system that serves no memory container fails instead of reporting 0 MiB."""
    _hmc_env(monkeypatch)
    _mock_bare_system(mock_hmc, "AssociatedSystemMemoryConfiguration")

    with pytest.raises(ValueError, match="AssociatedSystemMemoryConfiguration"):
        hmc_system_summary(SYSTEM_UUID)


def test_system_summary_degrades_when_the_vios_feed_fails(monkeypatch, mock_hmc):
    """A V11R2 HMC answers the system's VIOS feed with HTTP 500 (#1202).

    The summary keeps every figure the other reads returned and names the
    missing source, instead of failing as "unhandled errors in a TaskGroup".
    """
    _hmc_env(monkeypatch)
    refused = live_fixture("rest-vios-feed-500-v11r2")
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}").mock(
        return_value=httpx.Response(200, text=_system_feed(SYSTEM_UUID, "p9-prod"))
    )
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition").mock(
        return_value=httpx.Response(
            200, text=_lpar_feed((LPAR_UUID_1, "aix-prod", "running"))
        )
    )
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/VirtualIOServer").mock(
        return_value=httpx.Response(
            refused["status"],
            text=refused["body"],
            headers={"Content-Type": refused["content_type"]},
        )
    )

    result = hmc_system_summary(SYSTEM_UUID)

    assert result.lpar_count == 1
    assert result.vios_count is None
    (warning,) = result.warnings
    assert warning.startswith("VIOS inventory is unavailable")
    assert "HTTP 500" in warning and "ViosStorage" in warning
