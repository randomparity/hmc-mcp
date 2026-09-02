"""Tool-layer tests for actionable HTTP 406 messages on virtual-network write tools.

hmc_create_virtual_network calls a UOM PUT path that returns HTTP 406 on
header or schema-version mismatches.  These tests verify that the 406 is
translated into an actionable HMCError message (issue #96).
"""

from __future__ import annotations

import httpx
import pytest

from hmc_mcp.errors import HMCError
from hmc_mcp.server_tools.network import (
    hmc_create_virtual_network as hmc_create_virtual_network,
)

SYSTEM_UUID = "00000000-0000-0000-0000-000000000001"

SYSTEM_ENTRY = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:{SYSTEM_UUID}</id>
  <title>ManagedSystem:sys1</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <ManagedSystem xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <SystemName>sys1</SystemName>
    </ManagedSystem>
  </content>
</entry>
"""


def _hmc_env(monkeypatch) -> None:
    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "abc123")


# ---------------------------------------------------------------------- #
# hmc_create_virtual_network — HTTP 406 actionable error
# ---------------------------------------------------------------------- #


def test_create_virtual_network_http_406_actionable(monkeypatch, mock_hmc):
    """hmc_create_virtual_network returns an actionable message on HTTP 406."""
    _hmc_env(monkeypatch)
    # system UUID resolution
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}").mock(
        return_value=httpx.Response(200, text=SYSTEM_ENTRY)
    )
    # create returns 406
    mock_hmc.put(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/VirtualNetwork").mock(
        return_value=httpx.Response(406, text="<error>Not Acceptable</error>")
    )

    with pytest.raises(HMCError) as exc_info:
        hmc_create_virtual_network(
            system_name_or_uuid=SYSTEM_UUID,
            name="VLAN100-ETHERNET0",
            vlan_id=100,
            virtual_switch_id=3,
        )

    assert exc_info.value.status_code == 406
    msg = str(exc_info.value)
    assert "406" in msg
    assert "HMC_SCHEMA_VERSION" in msg or "schema" in msg.lower()
