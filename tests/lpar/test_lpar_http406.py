"""Tool-layer tests for HTTP 406 behaviour on LPAR write tools.

hmc_create_lpar falls back to the CLI (mksyscfg over SSH) when REST returns
HTTP 406, rather than raising.  hmc_modify_lpar and the DLPAR tools have no
CLI fallback and still surface an actionable HMCError on 406.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from hmc_mcp.client import HMCError
from hmc_mcp.documents import LparResources
from hmc_mcp.operations_lpar import _system_name
from hmc_mcp.ssh import HMCCLIError
from hmc_mcp.server import (
    hmc_create_lpar,
    hmc_dlpar_mem,
    hmc_dlpar_proc,
    hmc_modify_lpar,
)

SYSTEM_UUID = "00000000-0000-0000-0000-000000000001"
LPAR_UUID = "00000000-0000-0000-0000-000000000002"


@pytest.mark.asyncio
async def test_system_name_propagates_unexpected_rest_failure():
    hmc = AsyncMock()
    hmc.get_managed_system.side_effect = TypeError("programming defect")

    with pytest.raises(TypeError, match="programming defect"):
        await _system_name(hmc, SYSTEM_UUID, "fallback")


@pytest.mark.asyncio
async def test_system_name_uses_fallback_only_for_expected_lookup_failures():
    hmc = AsyncMock()
    hmc.get_managed_system.side_effect = HMCError("REST unavailable")

    with patch(
        "hmc_mcp.operations_lpar._ssh_system_name",
        new=AsyncMock(side_effect=HMCCLIError("SSH unavailable")),
    ):
        assert await _system_name(hmc, SYSTEM_UUID, "fallback") == "fallback"


EMPTY_FEED = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><feed xmlns="http://www.w3.org/2005/Atom"/>'

LPAR_ENTRY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:{uuid}</id>
  <title>LogicalPartition:lpar1</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <LogicalPartition xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <PartitionName>lpar1</PartitionName>
      <PartitionState>not activated</PartitionState>
    </LogicalPartition>
  </content>
</entry>
""".format(uuid=LPAR_UUID)

SYSTEM_ENTRY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:{uuid}</id>
  <title>ManagedSystem:sys1</title>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <ManagedSystem xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <SystemName>sys1</SystemName>
    </ManagedSystem>
  </content>
</entry>
""".format(uuid=SYSTEM_UUID)


def _hmc_env(monkeypatch) -> None:
    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "abc123")


# ---------------------------------------------------------------------- #
# hmc_create_lpar — HTTP 406 triggers CLI fallback
# ---------------------------------------------------------------------- #


def test_create_lpar_http_406_falls_back_to_cli(monkeypatch, mock_hmc):
    """hmc_create_lpar falls back to mksyscfg CLI when REST returns 406.

    The CLI fallback calls create_lpar_via_cli (SSH) instead of raising.
    After the CLI creates the partition, the tool fetches the new entry
    via REST and returns it.
    """
    _hmc_env(monkeypatch)

    # Round 1: no existing LPAR with this name (pre-create check)
    # Round 2: LPAR exists after CLI creation (post-create fetch)
    search_responses = [
        httpx.Response(200, text=EMPTY_FEED),
        httpx.Response(200, text=LPAR_ENTRY),
    ]
    mock_hmc.get(
        "/rest/api/uom/LogicalPartition/search/(PartitionName==new-lpar)"
    ).mock(side_effect=search_responses)
    # system UUID resolution
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}").mock(
        return_value=httpx.Response(200, text=SYSTEM_ENTRY)
    )
    # create returns 406 → triggers CLI fallback
    mock_hmc.put(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition").mock(
        return_value=httpx.Response(406, text="<error>Not Acceptable</error>")
    )

    # Patch CLI helpers and the stamp (stamp makes SSH call that would fail here).
    with (
        patch(
            "hmc_mcp.operations_lpar._ssh_system_name",
            new=AsyncMock(return_value="sys1"),
        ),
        patch(
            "hmc_mcp.operations_lpar.create_lpar_via_cli",
            new=AsyncMock(return_value=""),
        ) as create_via_cli,
        patch(
            "hmc_mcp.operations_lpar.stamp_lpar_ownership",
            new=AsyncMock(return_value="tok"),
        ),
    ):
        result = hmc_create_lpar(system_name_or_uuid=SYSTEM_UUID, name="new-lpar")

    # result is now wrapped: {"lpar": <entry>, "ownership_stamped": ..., "warnings": []}
    assert result is not None
    assert result.lpar.get("UUID") == LPAR_UUID
    create_via_cli.assert_awaited_once()
    resources = create_via_cli.await_args.kwargs["resources"]
    assert isinstance(resources, LparResources)
    assert resources.desired_memory == 4096
    assert resources.desired_vcpus == 1


# ---------------------------------------------------------------------- #
# hmc_modify_lpar — HTTP 406 actionable error
# ---------------------------------------------------------------------- #


def test_modify_lpar_http_406_actionable(monkeypatch, mock_hmc):
    """hmc_modify_lpar returns an actionable message on HTTP 406."""
    _hmc_env(monkeypatch)
    # LPAR UUID resolution
    mock_hmc.get(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}").mock(
        return_value=httpx.Response(200, text=LPAR_ENTRY)
    )
    # modify returns 406
    mock_hmc.post(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}").mock(
        return_value=httpx.Response(406, text="<error>Not Acceptable</error>")
    )

    with pytest.raises(HMCError) as exc_info:
        hmc_modify_lpar(
            lpar_name_or_uuid=LPAR_UUID,
            resources=LparResources(desired_memory=8192),
        )

    assert exc_info.value.status_code == 406
    msg = str(exc_info.value)
    assert "406" in msg
    assert "HMC_SCHEMA_VERSION" in msg or "schema" in msg.lower()


# ---------------------------------------------------------------------- #
# hmc_dlpar_proc — HTTP 406 actionable error
# ---------------------------------------------------------------------- #


def test_dlpar_proc_http_406_actionable(monkeypatch, mock_hmc):
    """hmc_dlpar_proc returns an actionable message on HTTP 406."""
    _hmc_env(monkeypatch)
    # LPAR UUID resolution
    mock_hmc.get(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}").mock(
        return_value=httpx.Response(200, text=LPAR_ENTRY)
    )
    # DLPAR POST returns 406
    mock_hmc.post(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}").mock(
        return_value=httpx.Response(406, text="<error>Not Acceptable</error>")
    )

    with pytest.raises(HMCError) as exc_info:
        hmc_dlpar_proc(
            lpar_name_or_uuid=LPAR_UUID,
            resources=LparResources(desired_procs=0.5),
        )

    assert exc_info.value.status_code == 406
    msg = str(exc_info.value)
    assert "406" in msg
    assert "HMC_SCHEMA_VERSION" in msg or "schema" in msg.lower()


# ---------------------------------------------------------------------- #
# hmc_dlpar_mem — HTTP 406 actionable error
# ---------------------------------------------------------------------- #


def test_dlpar_mem_http_406_actionable(monkeypatch, mock_hmc):
    """hmc_dlpar_mem returns an actionable message on HTTP 406."""
    _hmc_env(monkeypatch)
    # LPAR UUID resolution
    mock_hmc.get(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}").mock(
        return_value=httpx.Response(200, text=LPAR_ENTRY)
    )
    # DLPAR POST returns 406
    mock_hmc.post(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}").mock(
        return_value=httpx.Response(406, text="<error>Not Acceptable</error>")
    )

    with pytest.raises(HMCError) as exc_info:
        hmc_dlpar_mem(
            lpar_name_or_uuid=LPAR_UUID,
            resources=LparResources(desired_memory=8192),
        )

    assert exc_info.value.status_code == 406
    msg = str(exc_info.value)
    assert "406" in msg
    assert "HMC_SCHEMA_VERSION" in msg or "schema" in msg.lower()
