"""Tests for the hmc_system_summary composite tool.

Exercises the tool against a mocked HMC (respx) so the URL mapping and
field-extraction logic in server_tools/composite.py is verified without a live HMC.
"""

from __future__ import annotations

import httpx
import pytest

from hmc_mcp.server_tools.composite import hmc_system_summary as hmc_system_summary

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


def _system_feed(uuid: str, **fields: str) -> str:
    body = "\n".join(
        f'        <{k} xmlns="{NS}">{v}</{k}>'
        for k, v in fields.items()
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:{uuid}</id>
    <title>ManagedSystem:{uuid}</title>
    <link rel="SELF" href="https://hmc.test:12443/rest/api/uom/ManagedSystem/{uuid}"/>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <ManagedSystem xmlns="{NS}">
{body}
      </ManagedSystem>
    </content>
  </entry>
</feed>
"""


def _lpar_feed(*entries: tuple[str, str, str, str]) -> str:
    """entries: (uuid, name, state, memory) tuples."""
    parts = []
    for uuid, name, state, memory in entries:
        parts.append(f"""  <entry>
    <id>urn:uuid:{uuid}</id>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <LogicalPartition xmlns="{NS}">
        <PartitionName>{name}</PartitionName>
        <PartitionState>{state}</PartitionState>
        <DesiredMemory>{memory}</DesiredMemory>
        <DesiredProcessingUnits>0.5</DesiredProcessingUnits>
      </LogicalPartition>
    </content>
  </entry>""")
    joined = "\n".join(parts)
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
            text=_system_feed(
                SYSTEM_UUID,
                SystemName="p9-prod",
                State="operating",
                MachineTypeModelSerialNumber="9009-41A*12345AB",
                SystemFirmware="FW950.10",
                AssignableSystemMemory="131072",
                ConfigurableSystemProcessorUnits="16.0",
            ),
        )
    )
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition").mock(
        return_value=httpx.Response(
            200,
            text=_lpar_feed(
                (LPAR_UUID_1, "aix-prod", "running", "8192"),
                (LPAR_UUID_2, "linux-web", "not activated", "4096"),
            ),
        )
    )
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/VirtualIOServer").mock(
        return_value=httpx.Response(200, text=_vios_feed(VIOS_UUID_1, VIOS_UUID_2))
    )

    result = hmc_system_summary(SYSTEM_UUID)

    assert result["uuid"] == SYSTEM_UUID
    assert result["name"] == "p9-prod"
    assert result["state"] == "operating"
    assert result["mtms"] == "9009-41A*12345AB"
    assert result["firmware_version"] == "FW950.10"
    assert result["total_memory_mb"] == 131072
    assert result["free_memory_mb"] == 131072 - 8192 - 4096
    assert result["total_proc_units"] == 16.0
    assert result["free_proc_units"] == pytest.approx(16.0 - 1.0)
    assert result["lpar_count"] == 2
    assert result["lpar_states"] == {"running": 1, "not activated": 1}
    assert result["vios_count"] == 2


def test_system_summary_no_lpars_no_vios(monkeypatch, mock_hmc):
    """hmc_system_summary with empty LPAR + VIOS feeds returns zero counts."""
    _hmc_env(monkeypatch)
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}").mock(
        return_value=httpx.Response(
            200,
            text=_system_feed(
                SYSTEM_UUID,
                SystemName="empty-sys",
                State="standby",
                AssignableSystemMemory="65536",
                ConfigurableSystemProcessorUnits="8.0",
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

    assert result["lpar_count"] == 0
    assert result["lpar_states"] == {}
    assert result["vios_count"] == 0
    assert result["free_memory_mb"] == 65536
    assert result["free_proc_units"] == pytest.approx(8.0)


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
            text=_system_feed(SYSTEM_UUID, SystemName="p9-prod", State="operating"),
        )
    )
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition").mock(
        return_value=httpx.Response(200, text=EMPTY_FEED)
    )
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/VirtualIOServer").mock(
        return_value=httpx.Response(200, text=EMPTY_FEED)
    )

    result = hmc_system_summary("p9-prod")

    assert result["name"] == "p9-prod"
    assert result["uuid"] == SYSTEM_UUID
    assert result["state"] == "operating"


def test_system_summary_name_not_found_raises(monkeypatch, mock_hmc):
    """hmc_system_summary raises ValueError when the system name is unknown."""
    _hmc_env(monkeypatch)
    mock_hmc.get("/rest/api/uom/ManagedSystem/search/(SystemName==ghost-sys)").mock(
        return_value=httpx.Response(200, text=EMPTY_FEED)
    )

    with pytest.raises(ValueError, match="ghost-sys"):
        hmc_system_summary("ghost-sys")


def test_system_summary_missing_optional_fields(monkeypatch, mock_hmc):
    """hmc_system_summary tolerates a system entry missing optional fields."""
    _hmc_env(monkeypatch)
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}").mock(
        return_value=httpx.Response(
            200,
            text=_system_feed(SYSTEM_UUID, SystemName="bare-sys", State="operating"),
        )
    )
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition").mock(
        return_value=httpx.Response(200, text=EMPTY_FEED)
    )
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/VirtualIOServer").mock(
        return_value=httpx.Response(200, text=EMPTY_FEED)
    )

    result = hmc_system_summary(SYSTEM_UUID)

    assert result["mtms"] is None
    assert result["firmware_version"] is None
    assert result["total_memory_mb"] == 0
    assert result["total_proc_units"] == 0.0
