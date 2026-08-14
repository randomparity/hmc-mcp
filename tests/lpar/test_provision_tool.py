"""Tool-layer tests for hmc_provision_lpar.

Covers the full 5-step happy path, dry-run precondition-only mode,
precondition failures (name conflict, VLAN absent, VG absent), partial step
failure with skipped remainder, and the power_on=False variant.  All HTTP
interactions are mocked with the respx ``mock_hmc`` fixture from conftest.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from hmc_mcp.server import hmc_provision_lpar
from conftest import JOB_ENTRY


@pytest.fixture(autouse=True)
def _patch_stamp_ownership():
    """Stub out ownership stamping in all provision tests.

    stamp_lpar_ownership makes an SSH call; provision tests use respx (HTTP
    only) and must not attempt real SSH connections to hmc.test.
    """
    with patch(
        "hmc_mcp.operations_provision.stamp_lpar_ownership",
        new=AsyncMock(return_value="[hmc-mcp owner:hmc-mcp created:2026-08-13]"),
    ):
        yield

SYSTEM_UUID = "00000000-0000-0000-0000-000000000001"
LPAR_UUID = "00000000-0000-0000-0000-000000000002"
VIOS_UUID = "00000000-0000-0000-0000-000000000003"
ADAPTER_UUID = "adapter-uuid-0001"
VG_UUID = "vg-uuid-0001"
VLAN_ID = 100


def _hmc_env(monkeypatch) -> None:
    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.setenv("HMC_USER", "hscroot")
    monkeypatch.setenv("HMC_PASSWORD", "abc123")


# ---------------------------------------------------------------------- #
# Atom feed helpers
# ---------------------------------------------------------------------- #

EMPTY_FEED = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><feed xmlns="http://www.w3.org/2005/Atom"/>'

EXISTING_LPAR_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:{lpar_uuid}</id>
    <title>LogicalPartition:existing-lpar</title>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <LogicalPartition xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <PartitionName>existing-lpar</PartitionName>
        <PartitionState>not activated</PartitionState>
      </LogicalPartition>
    </content>
  </entry>
</feed>
""".format(lpar_uuid=LPAR_UUID)

CREATED_LPAR_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:{lpar_uuid}</id>
    <title>LogicalPartition:web01</title>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <LogicalPartition xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <PartitionName>web01</PartitionName>
        <PartitionState>not activated</PartitionState>
      </LogicalPartition>
    </content>
  </entry>
</feed>
""".format(lpar_uuid=LPAR_UUID)

NETWORK_ADAPTER_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:{uuid}</id>
    <title>ClientNetworkAdapter:{uuid}</title>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <ClientNetworkAdapter xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <MACAddress>aa:bb:cc:dd:ee:ff</MACAddress>
      </ClientNetworkAdapter>
    </content>
  </entry>
</feed>
""".format(uuid=ADAPTER_UUID)

VSCSI_ADAPTER_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:{uuid}</id>
    <title>VirtualSCSIClientAdapter:{uuid}</title>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <VirtualSCSIClientAdapter xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <RemoteLogicalPartitionID>7</RemoteLogicalPartitionID>
      </VirtualSCSIClientAdapter>
    </content>
  </entry>
</feed>
""".format(uuid=ADAPTER_UUID)

VIOS_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:{vios_uuid}</id>
    <title>VirtualIOServer:vios1</title>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <VirtualIOServer xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <PartitionName>vios1</PartitionName>
      </VirtualIOServer>
    </content>
  </entry>
</feed>
""".format(vios_uuid=VIOS_UUID)

VG_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:{vg_uuid}</id>
    <title>VolumeGroup:{vg_uuid}</title>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <VolumeGroup xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <GroupName>rootvg</GroupName>
      </VolumeGroup>
    </content>
  </entry>
</feed>
""".format(vg_uuid=VG_UUID)

VLAN_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:vn-uuid-0001</id>
    <title>VirtualNetwork:vn-uuid-0001</title>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <VirtualNetwork xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <NetworkVLANID>100</NetworkVLANID>
        <NetworkName>VLAN100</NetworkName>
      </VirtualNetwork>
    </content>
  </entry>
</feed>
"""

SYSTEM_FEED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:uuid:{system_uuid}</id>
    <title>ManagedSystem:sys1</title>
    <content type="application/vnd.ibm.powervm.uom+xml">
      <ManagedSystem xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
        <SystemName>sys1</SystemName>
      </ManagedSystem>
    </content>
  </entry>
</feed>
""".format(system_uuid=SYSTEM_UUID)


def _mock_preconditions(mock_hmc, *, name="web01", has_lpar=False, has_vlan=True, has_vg=True):
    """Register the three precondition GET routes."""
    # Support both "web01" (default) and a custom name such as "existing-lpar"
    # by registering the search URL for whatever name was requested.
    lpar_feed_text = EXISTING_LPAR_FEED if has_lpar else EMPTY_FEED
    mock_hmc.get(
        f"/rest/api/uom/LogicalPartition/search/(PartitionName=={name})"
    ).mock(
        return_value=httpx.Response(200, text=lpar_feed_text)
    )
    mock_hmc.get(
        f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/VirtualNetwork"
    ).mock(
        return_value=httpx.Response(200, text=VLAN_FEED if has_vlan else EMPTY_FEED)
    )
    mock_hmc.get(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup"
    ).mock(
        return_value=httpx.Response(200, text=VG_FEED if has_vg else EMPTY_FEED)
    )


SYSTEM_ENTRY = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<entry xmlns="http://www.w3.org/2005/Atom">
  <id>urn:uuid:{system_uuid}</id>
  <content type="application/vnd.ibm.powervm.uom+xml">
    <ManagedSystem xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/">
      <SystemName>sys1</SystemName>
    </ManagedSystem>
  </content>
</entry>""".format(system_uuid=SYSTEM_UUID)


def _mock_execution_steps(mock_hmc):
    """Register the 5 execution step routes (create, network, vscsi, storage, power-on)."""
    mock_hmc.put(
        f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition"
    ).mock(return_value=httpx.Response(201, text=CREATED_LPAR_FEED))
    # get_managed_system for stamp system-name resolution (REST-first)
    mock_hmc.get(
        f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}"
    ).mock(return_value=httpx.Response(200, text=SYSTEM_ENTRY))

    mock_hmc.put(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/ClientNetworkAdapter"
    ).mock(return_value=httpx.Response(201, text=NETWORK_ADAPTER_FEED))

    mock_hmc.put(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/VirtualSCSIClientAdapter"
    ).mock(return_value=httpx.Response(201, text=VSCSI_ADAPTER_FEED))

    mock_hmc.post(
        f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}"
    ).mock(return_value=httpx.Response(201, text=VIOS_FEED))

    mock_hmc.put(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/do/PowerOn"
    ).mock(return_value=httpx.Response(202, text=JOB_ENTRY))


# ---------------------------------------------------------------------- #
# Helper: common provision arguments
# ---------------------------------------------------------------------- #

def _provision_args(**overrides):
    args = dict(
        system_name_or_uuid=SYSTEM_UUID,
        name="web01",
        port_vlan_id=VLAN_ID,
        vios_uuid=VIOS_UUID,
        vios_partition_id=7,
        vios_slot=11,
        storage_name="lv_boot",
    )
    args.update(overrides)
    return args


# ---------------------------------------------------------------------- #
# Full happy-path workflow
# ---------------------------------------------------------------------- #


def test_provision_lpar_full_workflow(monkeypatch, mock_hmc):
    """hmc_provision_lpar executes all 5 steps and returns structured results."""
    _hmc_env(monkeypatch)
    _mock_preconditions(mock_hmc)
    _mock_execution_steps(mock_hmc)

    result = hmc_provision_lpar(**_provision_args())

    assert result["created"] is True
    assert result["dry_run"] is False
    steps = {s["step"]: s for s in result["steps"]}
    assert steps["create"]["status"] == "ok"
    assert steps["network"]["status"] == "ok"
    assert steps["vscsi"]["status"] == "ok"
    assert steps["storage"]["status"] == "ok"
    assert steps["power_on"]["status"] == "ok"
    assert isinstance(result["warnings"], list)


def test_provision_lpar_step_results_contain_data(monkeypatch, mock_hmc):
    """Each ok step's result field contains the parsed HMC response."""
    _hmc_env(monkeypatch)
    _mock_preconditions(mock_hmc)
    _mock_execution_steps(mock_hmc)

    result = hmc_provision_lpar(**_provision_args())

    steps = {s["step"]: s for s in result["steps"]}
    # create step should contain partition data
    create_result = steps["create"]["result"]
    assert create_result is not None
    assert create_result.get("Resource", {}).get("PartitionName") == "web01"


# ---------------------------------------------------------------------- #
# power_on=False
# ---------------------------------------------------------------------- #


def test_provision_lpar_no_power_on(monkeypatch, mock_hmc):
    """power_on=False skips the PowerOn job step entirely."""
    _hmc_env(monkeypatch)
    _mock_preconditions(mock_hmc)
    _mock_execution_steps(mock_hmc)

    result = hmc_provision_lpar(**_provision_args(power_on=False))

    assert result["created"] is True
    step_names = [s["step"] for s in result["steps"]]
    assert "power_on" not in step_names


# ---------------------------------------------------------------------- #
# Dry-run
# ---------------------------------------------------------------------- #


def test_provision_lpar_dry_run_validates_only(monkeypatch, mock_hmc):
    """dry_run=True checks preconditions but issues no mutating requests."""
    _hmc_env(monkeypatch)
    _mock_preconditions(mock_hmc)

    # Register execution routes but they should NOT be called
    create_route = mock_hmc.put(
        f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition"
    ).mock(return_value=httpx.Response(201, text=CREATED_LPAR_FEED))

    result = hmc_provision_lpar(**_provision_args(dry_run=True))

    assert result["dry_run"] is True
    assert result["created"] is False
    assert not create_route.called
    # All steps report dry_run status
    for step in result["steps"]:
        assert step["status"] == "dry_run"


def test_provision_lpar_dry_run_name_conflict(monkeypatch, mock_hmc):
    """dry_run=True with a conflicting name surfaces the error without creating."""
    _hmc_env(monkeypatch)
    _mock_preconditions(mock_hmc, name="existing-lpar", has_lpar=True)

    with pytest.raises(ValueError, match="existing-lpar"):
        hmc_provision_lpar(**_provision_args(name="existing-lpar", dry_run=True))


# ---------------------------------------------------------------------- #
# Precondition failures (non-dry-run)
# ---------------------------------------------------------------------- #


def test_provision_lpar_name_conflict_aborts(monkeypatch, mock_hmc):
    """Name collision raises ValueError before any execution step."""
    _hmc_env(monkeypatch)
    _mock_preconditions(mock_hmc, name="existing-lpar", has_lpar=True)

    create_route = mock_hmc.put(
        f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition"
    )

    with pytest.raises(ValueError, match="existing-lpar"):
        hmc_provision_lpar(**_provision_args(name="existing-lpar"))

    assert not create_route.called


def test_provision_lpar_vlan_not_found(monkeypatch, mock_hmc):
    """When the VLAN is not found on the system, a ValueError is raised."""
    _hmc_env(monkeypatch)
    _mock_preconditions(mock_hmc, has_vlan=False)

    with pytest.raises(ValueError, match="[Vv][Ll][Aa][Nn]|port_vlan_id|network"):
        hmc_provision_lpar(**_provision_args())


def test_provision_lpar_vg_not_found(monkeypatch, mock_hmc):
    """When the volume group is not found, a ValueError is raised."""
    _hmc_env(monkeypatch)
    _mock_preconditions(mock_hmc, has_vg=False)

    with pytest.raises(ValueError, match="[Vv]olume|vg_uuid|VolumeGroup"):
        hmc_provision_lpar(**_provision_args(vg_uuid=VG_UUID))


# ---------------------------------------------------------------------- #
# Partial step failure
# ---------------------------------------------------------------------- #


def test_provision_lpar_partial_failure_skips_remaining(monkeypatch, mock_hmc):
    """When the vscsi step fails, storage and power_on are recorded as skipped."""
    _hmc_env(monkeypatch)
    _mock_preconditions(mock_hmc)

    mock_hmc.put(
        f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition"
    ).mock(return_value=httpx.Response(201, text=CREATED_LPAR_FEED))

    mock_hmc.put(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/ClientNetworkAdapter"
    ).mock(return_value=httpx.Response(201, text=NETWORK_ADAPTER_FEED))

    # vSCSI step fails
    mock_hmc.put(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/VirtualSCSIClientAdapter"
    ).mock(return_value=httpx.Response(500, text="<error>vscsi failed</error>"))

    # storage and power_on should not be called
    storage_route = mock_hmc.post(f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}")
    power_on_route = mock_hmc.put(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/do/PowerOn"
    )

    result = hmc_provision_lpar(**_provision_args())

    steps = {s["step"]: s for s in result["steps"]}
    assert steps["create"]["status"] == "ok"
    assert steps["network"]["status"] == "ok"
    assert steps["vscsi"]["status"] == "error"
    assert steps["storage"]["status"] == "skipped"
    assert steps["power_on"]["status"] == "skipped"
    assert not storage_route.called
    assert not power_on_route.called
    # created is False when the workflow did not complete successfully
    assert result["created"] is False
