"""Tests for ownership token stamping in direct LPAR creation (issue #132)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import respx

from hmc_mcp.server import hmc_create_lpar

BASE = "https://hmc.test:12443"
SYSTEM_UUID = "aaaa0000-0000-0000-0000-000000000001"
LPAR_UUID = "bbbb0000-0000-0000-0000-000000000001"

LOGON_XML = """<?xml version="1.0" encoding="UTF-8"?>
<LogonResponse xmlns="http://www.ibm.com/xmlns/systems/power/firmware/web/mc/2012_10/">
  <X-API-Session>tok</X-API-Session>
</LogonResponse>"""

EMPTY_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom"/>"""

LPAR_ENTRY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:{uuid}</id>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <LogicalPartition xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <PartitionName>{name}</PartitionName>
      <PartitionState>not activated</PartitionState>
    </LogicalPartition>
  </content>
</entry>"""


def _env(monkeypatch, agent_id: str | None = "test-agent"):
    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "u")
    monkeypatch.setenv("HMC_PASSWORD", "p")
    if agent_id:
        monkeypatch.setenv("HMC_AGENT_ID", agent_id)


def _setup_mock(router):
    """Register the standard logon/logoff + system UUID resolve + name check stubs."""
    router.put("/rest/api/web/Logon").mock(
        return_value=httpx.Response(200, text=LOGON_XML)
    )
    router.delete("/rest/api/web/Logon").mock(return_value=httpx.Response(204))
    # Name-uniqueness check — no existing LPAR
    router.get("/rest/api/uom/LogicalPartition/search/(PartitionName==test-lpar)").mock(
        return_value=httpx.Response(200, text=EMPTY_FEED)
    )
    # System UUID lookup (resolve_system_uuid called with a UUID → direct GET)
    router.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}").mock(
        return_value=httpx.Response(
            200,
            text=f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:{SYSTEM_UUID}</id>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <ManagedSystem xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <SystemName>server1</SystemName>
    </ManagedSystem>
  </content>
</entry>""",
        )
    )
    # REST LPAR create
    router.put(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition").mock(
        return_value=httpx.Response(
            201,
            text=LPAR_ENTRY.format(uuid=LPAR_UUID, name="test-lpar"),
        )
    )


# ---------------------------------------------------------------------- #
# Stamping success
# ---------------------------------------------------------------------- #


def test_create_lpar_ownership_stamped_true(monkeypatch):
    """When stamp succeeds, result has ownership_stamped=True and empty warnings."""
    _env(monkeypatch)
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        _setup_mock(router)
        with patch(
            "hmc_mcp.operations_lpar.stamp_lpar_ownership",
            new=AsyncMock(return_value="[hmc-mcp owner:test-agent created:2026-08-13]"),
        ):
            result = hmc_create_lpar(
                system_name_or_uuid=SYSTEM_UUID,
                name="test-lpar",
            )

    assert isinstance(result, dict)
    assert "lpar" in result
    assert result["ownership_stamped"] is True
    assert result["warnings"] == []
    # The lpar key carries the created partition entry
    assert result["lpar"]["Resource"]["PartitionName"] == "test-lpar"


# ---------------------------------------------------------------------- #
# Stamp failure
# ---------------------------------------------------------------------- #


def test_create_lpar_ownership_stamped_false_on_stamp_failure(monkeypatch):
    """When stamp returns None, ownership_stamped=False and warnings has a message."""
    _env(monkeypatch)
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        _setup_mock(router)
        with patch(
            "hmc_mcp.operations_lpar.stamp_lpar_ownership",
            new=AsyncMock(return_value=None),
        ):
            result = hmc_create_lpar(
                system_name_or_uuid=SYSTEM_UUID,
                name="test-lpar",
            )

    assert result["ownership_stamped"] is False
    assert len(result["warnings"]) == 1
    assert "stamp" in result["warnings"][0].lower()
    # LPAR is still returned
    assert result["lpar"] is not None


# ---------------------------------------------------------------------- #
# Return shape always present (even when no agent_id set)
# ---------------------------------------------------------------------- #


def test_create_lpar_result_shape_without_agent_id(monkeypatch):
    """Without HMC_AGENT_ID, result still has ownership_stamped and warnings keys."""
    _env(monkeypatch, agent_id=None)  # no HMC_AGENT_ID
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        _setup_mock(router)
        with patch(
            "hmc_mcp.operations_lpar.stamp_lpar_ownership",
            new=AsyncMock(return_value="[hmc-mcp owner:hmc-mcp created:2026-08-13]"),
        ):
            result = hmc_create_lpar(
                system_name_or_uuid=SYSTEM_UUID,
                name="test-lpar",
            )

    assert "lpar" in result
    assert "ownership_stamped" in result
    assert "warnings" in result
    assert result["ownership_stamped"] is True
