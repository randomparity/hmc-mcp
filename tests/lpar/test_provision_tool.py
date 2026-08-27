"""Tool-layer tests for hmc_provision_lpar.

Covers the full 5-step happy path, dry-run precondition-only mode,
precondition failures (name conflict, VLAN absent, VG absent), partial step
failure with skipped remainder, and the power_on=False variant.  All HTTP
interactions are mocked with the respx ``mock_hmc`` fixture from conftest.py.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import ANY, AsyncMock, patch

import httpx
import pytest
from conftest import JOB_ENTRY, assert_no_mutating_requests

from hmc_mcp.documents import LparResources
from hmc_mcp.jobs import JobOutcome
from hmc_mcp.operations.assignments import WorkflowStep
from hmc_mcp.operations.lpar import LparPowerResult
from hmc_mcp.operations.provision import (
    ProvisionAffinityAssessment,
    ProvisionNetwork,
    ProvisionStorage,
    _power_on,
)
from hmc_mcp.server_tools.provision import hmc_provision_lpar as hmc_provision_lpar
from hmc_mcp.snapshots.affinity import (
    AffinityAssessmentResult,
    AffinityEvidence,
    PostActivationAffinityAssessment,
    validate_affinity_request,
)
from hmc_mcp.ssh.affinity import MinimumAffinityPolicy
from hmc_mcp.ssh.transport import HMCCLIError


@pytest.fixture(autouse=True)
def _patch_stamp_ownership():
    """Stub out ownership stamping in all provision tests.

    stamp_lpar_ownership makes an SSH call; provision tests use respx (HTTP
    only) and must not attempt real SSH connections to hmc.test.
    """
    with patch(
        "hmc_mcp.operations.lpar_ownership.stamp_lpar_ownership",
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

MALFORMED_VLAN_FEED = VLAN_FEED.replace(
    "<NetworkVLANID>100</NetworkVLANID>",
    "<NetworkVLANID>not-a-vlan</NetworkVLANID>",
).replace("VLAN100", "broken-network")

VALID_VLAN_ENTRY = (
    "<entry>" + VLAN_FEED.split("<entry>", 1)[1].split("</entry>", 1)[0] + "</entry>"
)
MALFORMED_THEN_VALID_VLAN_FEED = MALFORMED_VLAN_FEED.replace(
    "</feed>", f"{VALID_VLAN_ENTRY}\n</feed>"
)

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


def _mock_preconditions(
    mock_hmc,
    *,
    name="web01",
    has_lpar=False,
    has_vlan=True,
    has_vg=True,
    vlan_feed=None,
):
    """Register the three precondition GET routes."""
    # Support both "web01" (default) and a custom name such as "existing-lpar"
    # by registering the search URL for whatever name was requested.
    lpar_feed_text = EXISTING_LPAR_FEED if has_lpar else EMPTY_FEED
    name_lookup = mock_hmc.get(
        f"/rest/api/uom/LogicalPartition/search/(PartitionName=={name})"
    ).mock(return_value=httpx.Response(200, text=lpar_feed_text))
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/VirtualNetwork").mock(
        return_value=httpx.Response(
            200, text=vlan_feed or (VLAN_FEED if has_vlan else EMPTY_FEED)
        )
    )
    mock_hmc.get(f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}/VolumeGroup").mock(
        return_value=httpx.Response(200, text=VG_FEED if has_vg else EMPTY_FEED)
    )
    return name_lookup


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
    create_route = mock_hmc.put(
        f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition"
    ).mock(return_value=httpx.Response(201, text=CREATED_LPAR_FEED))
    # get_managed_system for stamp system-name resolution (REST-first)
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}").mock(
        return_value=httpx.Response(200, text=SYSTEM_ENTRY)
    )

    mock_hmc.put(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/ClientNetworkAdapter"
    ).mock(return_value=httpx.Response(201, text=NETWORK_ADAPTER_FEED))

    mock_hmc.put(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/VirtualSCSIClientAdapter"
    ).mock(return_value=httpx.Response(201, text=VSCSI_ADAPTER_FEED))

    mock_hmc.post(f"/rest/api/uom/VirtualIOServer/{VIOS_UUID}").mock(
        return_value=httpx.Response(201, text=VIOS_FEED)
    )

    mock_hmc.put(f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/do/PowerOn").mock(
        return_value=httpx.Response(202, text=JOB_ENTRY)
    )
    return create_route


# ---------------------------------------------------------------------- #
# Helper: common provision arguments
# ---------------------------------------------------------------------- #


def _provision_args(**overrides):
    args = dict(
        system_name_or_uuid=SYSTEM_UUID,
        name="web01",
        network=ProvisionNetwork(
            port_vlan_id=VLAN_ID, vios_partition_id=7, vios_slot=11
        ),
        storage=ProvisionStorage(vios_uuid=VIOS_UUID, storage_name="lv_boot"),
        resources=LparResources(
            min_memory=256,
            desired_memory=4096,
            max_memory=8192,
            desired_vcpus=1,
            max_vcpus=2,
        ),
    )
    if "vg_uuid" in overrides:
        args["storage"] = ProvisionStorage(
            vios_uuid=VIOS_UUID,
            storage_name="lv_boot",
            vg_uuid=overrides.pop("vg_uuid"),
        )
    args.update(overrides)
    return args


def _affinity_request(**overrides):
    values = dict(
        system_name_or_uuid=SYSTEM_UUID,
        lpar_name="web01",
        captured_score=80,
        captured_policy_state="configured",
        captured_minimum=70,
        captured_at=datetime(2026, 8, 24, tzinfo=UTC),
        stale_after_seconds=86400,
        response="warn",
        regression_threshold=5,
        optimization_threshold=5,
        timeout_seconds=30,
        poll_interval=1,
    )
    values.update(overrides)
    return ProvisionAffinityAssessment(**values)


def test_provision_affinity_rejects_foreign_evidence_before_hmc(monkeypatch, mock_hmc):
    _hmc_env(monkeypatch)

    with pytest.raises(ValueError, match="assessment identities"):
        hmc_provision_lpar(
            **_provision_args(
                affinity_assessment=_affinity_request(lpar_name="another-lpar")
            )
        )

    assert_no_mutating_requests(mock_hmc)


@pytest.mark.parametrize(
    "change",
    [
        {"captured_score": 101},
        {"captured_at": datetime(2026, 8, 24)},
        {"stale_after_seconds": 0},
        {"regression_threshold": -1},
        {"captured_policy_state": "unknown"},
    ],
)
def test_provision_affinity_rejects_invalid_evidence_before_hmc(
    monkeypatch, mock_hmc, change
):
    _hmc_env(monkeypatch)
    with pytest.raises(ValueError):
        hmc_provision_lpar(
            **_provision_args(affinity_assessment=_affinity_request(**change))
        )
    assert_no_mutating_requests(mock_hmc)


def test_provision_affinity_requires_thresholds_without_applied_policy(
    monkeypatch, mock_hmc
):
    _hmc_env(monkeypatch)
    request = _affinity_request(
        captured_policy_state="unsupported",
        captured_minimum=None,
        regression_threshold=None,
        optimization_threshold=None,
    )
    with pytest.raises(ValueError, match="caller thresholds"):
        hmc_provision_lpar(**_provision_args(affinity_assessment=request))
    assert_no_mutating_requests(mock_hmc)


def test_provision_affinity_applied_policy_validates_against_captured_state(
    monkeypatch,
):
    _hmc_env(monkeypatch)
    request = _affinity_request(
        captured_policy_state="absent",
        captured_minimum=None,
        regression_threshold=None,
        optimization_threshold=None,
    )
    validate_affinity_request(request, 70)


def _assessment_result(classification="none"):
    evidence = AffinityEvidence(
        captured_score=80,
        current_score=82,
        predicted_score=90,
        policy_state="absent",
        captured_policy_state="absent",
        configured_minimum=None,
        captured_minimum=None,
        captured_at=datetime.now(UTC).isoformat(),
        assessed_at=datetime.now(UTC).isoformat(),
        stale_after_seconds=300,
        regression_threshold=None,
        optimization_threshold=None,
    )
    assessment = AffinityAssessmentResult(
        classification=classification,
        evidence=evidence,
        explanation="assessment",
        recommended_actions=(),
    )
    return PostActivationAffinityAssessment(assessment, 82, 90, False)


def _successful_power_outcome():
    return JobOutcome("job-1", "COMPLETED_OK", False, None, {"Resource": {}}, True, None)


def test_provision_affinity_power_on_waits_for_terminal_result():
    terminal_job = {"Resource": {"Status": "COMPLETED_OK"}}
    hmc = object()
    with patch(
        "hmc_mcp.operations.provision.power_lpar",
        new=AsyncMock(return_value=LparPowerResult(LPAR_UUID, terminal_job)),
    ) as power:
        result = asyncio.run(_power_on(hmc, SYSTEM_UUID, LPAR_UUID, _affinity_request()))  # type: ignore[arg-type]
    assert isinstance(result, JobOutcome)
    assert result.status == "COMPLETED_OK"
    assert result.timed_out is False
    power.assert_awaited_once_with(
        hmc,
        SYSTEM_UUID,
        LPAR_UUID,
        power_on=True,
        force=True,
        wait=True,
        timeout_seconds=30,
        poll_interval=1,
        ownership_override=True,
    )


def test_provision_affinity_dry_run_never_powers_on_or_assesses(monkeypatch, mock_hmc):
    _hmc_env(monkeypatch)
    _mock_preconditions(mock_hmc)
    with patch(
        "hmc_mcp.operations.provision.assess_post_activation_affinity",
        new=AsyncMock(),
    ) as assess:
        result = hmc_provision_lpar(
            **_provision_args(dry_run=True, affinity_assessment=_affinity_request())
        )
    assert result.steps[-1] == WorkflowStep("affinity_assessment", "dry_run")
    assess.assert_not_awaited()
    assert_no_mutating_requests(mock_hmc)


def test_provision_affinity_power_off_is_skipped(monkeypatch, mock_hmc):
    _hmc_env(monkeypatch)
    _mock_preconditions(mock_hmc)
    _mock_execution_steps(mock_hmc)
    with patch(
        "hmc_mcp.operations.provision.assess_post_activation_affinity",
        new=AsyncMock(),
    ) as assess:
        result = hmc_provision_lpar(
            **_provision_args(power_on=False, affinity_assessment=_affinity_request())
        )
    assert result.workflow_completed is False
    assert result.steps[-1] == WorkflowStep("affinity_assessment", "skipped")
    assess.assert_not_awaited()


@pytest.mark.parametrize(
    ("classification", "response", "completed", "status", "warning"),
    [
        ("none", "warn", True, "ok", False),
        ("policy-violation", "warn", True, "ok", True),
        ("unsupported-data", "warn", True, "ok", True),
        ("policy-violation", "fail", False, "error", False),
    ],
)
def test_provision_affinity_response_is_explicit(
    monkeypatch, mock_hmc, classification, response, completed, status, warning
):
    _hmc_env(monkeypatch)
    _mock_preconditions(mock_hmc)
    _mock_execution_steps(mock_hmc)
    with (
        patch(
            "hmc_mcp.operations.provision._power_on",
            new=AsyncMock(return_value=_successful_power_outcome()),
        ),
        patch(
            "hmc_mcp.operations.provision.assess_post_activation_affinity",
            new=AsyncMock(return_value=_assessment_result(classification)),
        ) as assess,
    ):
        result = hmc_provision_lpar(
            **_provision_args(affinity_assessment=_affinity_request(response=response))
        )
    assert result.workflow_completed is completed
    assert result.steps[-1].status == status
    assert result.steps[-1].result["achieved_score"] == 82
    assert result.steps[-1].result["predicted_score"] == 90
    assert result.steps[-1].result["prediction_guaranteed"] is False
    assert bool(result.warnings) is warning
    assess.assert_awaited_once()


def test_provision_affinity_timeout_never_assesses(monkeypatch, mock_hmc):
    _hmc_env(monkeypatch)
    _mock_preconditions(mock_hmc)
    _mock_execution_steps(mock_hmc)
    timed_out = JobOutcome("job-1", "RUNNING", True, None, {"Resource": {}}, True, None)
    with (
        patch(
            "hmc_mcp.operations.provision._power_on",
            new=AsyncMock(return_value=timed_out),
        ),
        patch(
            "hmc_mcp.operations.provision.assess_post_activation_affinity",
            new=AsyncMock(),
        ) as assess,
    ):
        result = hmc_provision_lpar(
            **_provision_args(affinity_assessment=_affinity_request())
        )
    assert result.workflow_completed is False
    assert result.steps[-2].status == "error"
    assert result.steps[-1] == WorkflowStep("affinity_assessment", "skipped")
    assess.assert_not_awaited()


def test_provision_keeps_its_result_when_the_power_guard_fails(monkeypatch, mock_hmc):
    """A guard resolution failure must not discard the created LPAR's identity.

    With ``authorize_power_operations`` on, the activation leg reaches the
    ADR 0011 guard, whose name resolution raises ``ValueError`` rather than
    ``HMCError``. Nothing rolls back the partition this workflow created, so
    losing the result that names it would leave the caller with nothing to
    clean up by (#371).
    """
    _hmc_env(monkeypatch)
    _mock_preconditions(mock_hmc)
    _mock_execution_steps(mock_hmc)
    with patch(
        "hmc_mcp.operations.provision._power_on",
        new=AsyncMock(side_effect=ValueError("LPAR 'x' has no partition name")),
    ):
        result = hmc_provision_lpar(**_provision_args())

    assert result.workflow_completed is False
    assert result.resource_created is True
    assert result.lpar_uuid == LPAR_UUID
    steps = {s.step: s for s in result.steps}
    assert steps["power_on"].status == "error"
    assert "no partition name" in steps["power_on"].result


# ---------------------------------------------------------------------- #
# Full happy-path workflow
# ---------------------------------------------------------------------- #


def test_provision_lpar_full_workflow(monkeypatch, mock_hmc):
    """hmc_provision_lpar executes all 5 steps and returns structured results."""
    _hmc_env(monkeypatch)
    name_lookup = _mock_preconditions(mock_hmc)
    _mock_execution_steps(mock_hmc)

    result = hmc_provision_lpar(**_provision_args())

    assert result.resource_created is True
    assert result.workflow_completed is True
    assert result.lpar_uuid == LPAR_UUID
    assert result.dry_run is False
    # The explicit uniqueness precondition and the create workflow's own
    # race-safe resolution each verify the name.
    assert name_lookup.call_count == 2
    steps = {s.step: s for s in result.steps}
    assert steps["create"].status == "ok"
    assert steps["network"].status == "ok"
    assert steps["vscsi"].status == "ok"
    assert steps["storage"].status == "ok"
    assert steps["power_on"].status == "ok"
    assert isinstance(result.warnings, tuple)


def test_provision_lpar_step_results_contain_data(monkeypatch, mock_hmc):
    """Each ok step's result field contains the parsed HMC response."""
    _hmc_env(monkeypatch)
    _mock_preconditions(mock_hmc)
    _mock_execution_steps(mock_hmc)

    result = hmc_provision_lpar(**_provision_args())

    steps = {s.step: s for s in result.steps}
    # create step should contain partition data
    create_result = steps["create"].result
    assert create_result is not None
    assert create_result.get("Resource", {}).get("PartitionName") == "web01"


@pytest.mark.parametrize("dedicated", [False, True])
def test_provision_lpar_preserves_complete_resource_input(
    monkeypatch, mock_hmc, dedicated
):
    _hmc_env(monkeypatch)
    _mock_preconditions(mock_hmc)
    create_route = _mock_execution_steps(mock_hmc)
    resources = LparResources(
        min_memory=512,
        desired_memory=2048,
        max_memory=4096,
        dedicated=dedicated,
        min_procs=0.5 if not dedicated else 1,
        desired_procs=1.0 if not dedicated else 2,
        max_procs=2.0 if not dedicated else 4,
        min_vcpus=1,
        desired_vcpus=2,
        max_vcpus=4,
        sharing_mode="uncapped" if not dedicated else None,
        uncapped=not dedicated,
    )

    result = hmc_provision_lpar(**_provision_args(resources=resources))

    assert result.workflow_completed is True
    body = create_route.calls.last.request.content.decode()
    for value in (512, 2048, 4096, 1, 2, 4):
        assert f">{value}<" in body
    if dedicated:
        assert "DedicatedProcessorConfiguration" in body
        assert "<DesiredProcessors" in body
    else:
        assert "SharedProcessorConfiguration" in body
        assert "<DesiredProcessingUnits" in body
        assert "uncapped" in body


# ---------------------------------------------------------------------- #
# power_on=False
# ---------------------------------------------------------------------- #


def test_provision_lpar_no_power_on(monkeypatch, mock_hmc):
    """power_on=False skips the PowerOn job step entirely."""
    _hmc_env(monkeypatch)
    _mock_preconditions(mock_hmc)
    _mock_execution_steps(mock_hmc)

    result = hmc_provision_lpar(**_provision_args(power_on=False))

    assert result.workflow_completed is True
    step_names = [s.step for s in result.steps]
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

    assert result.dry_run is True
    assert result.resource_created is False
    assert result.workflow_completed is False
    assert result.lpar_uuid is None
    assert not create_route.called
    # All steps report dry_run status
    for step in result.steps:
        assert step.status == "dry_run"


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


def test_provision_lpar_reports_missing_vlan_and_malformed_inventory(
    monkeypatch, mock_hmc
):
    _hmc_env(monkeypatch)
    _mock_preconditions(mock_hmc, vlan_feed=MALFORMED_VLAN_FEED)

    with pytest.raises(
        ValueError, match="No VirtualNetwork.*broken-network.*not-a-vlan"
    ):
        hmc_provision_lpar(**_provision_args())


def test_provision_lpar_accepts_valid_vlan_after_malformed_entry(monkeypatch, mock_hmc):
    _hmc_env(monkeypatch)
    _mock_preconditions(mock_hmc, vlan_feed=MALFORMED_THEN_VALID_VLAN_FEED)

    result = hmc_provision_lpar(**_provision_args(dry_run=True))

    assert result.dry_run is True


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

    mock_hmc.put(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition").mock(
        return_value=httpx.Response(201, text=CREATED_LPAR_FEED)
    )
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}").mock(
        return_value=httpx.Response(200, text=SYSTEM_ENTRY)
    )

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

    steps = {s.step: s for s in result.steps}
    assert steps["create"].status == "ok"
    assert steps["network"].status == "ok"
    assert steps["vscsi"].status == "error"
    assert steps["storage"].status == "skipped"
    assert steps["power_on"].status == "skipped"
    assert not storage_route.called
    assert not power_on_route.called
    assert result.resource_created is True
    assert result.workflow_completed is False
    assert result.lpar_uuid == LPAR_UUID


def test_policy_provision_network_failure_records_each_step_once(monkeypatch, mock_hmc):
    _hmc_env(monkeypatch)
    _mock_preconditions(mock_hmc)
    mock_hmc.put(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition").mock(
        return_value=httpx.Response(201, text=CREATED_LPAR_FEED)
    )
    mock_hmc.get(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}").mock(
        return_value=httpx.Response(200, text=SYSTEM_ENTRY)
    )
    mock_hmc.put(
        f"/rest/api/uom/LogicalPartition/{LPAR_UUID}/ClientNetworkAdapter"
    ).mock(return_value=httpx.Response(500, text="<error>network failed</error>"))
    with (
        patch(
            "hmc_mcp.operations.provision.resolve_ssh_names",
            AsyncMock(return_value=("system", None)),
        ),
        patch(
            "hmc_mcp.operations.provision.require_minimum_affinity_policy_capability",
            AsyncMock(),
        ),
        patch(
            "hmc_mcp.operations.provision.set_minimum_affinity_policy",
            AsyncMock(return_value="changed"),
        ),
    ):
        result = hmc_provision_lpar(
            **_provision_args(minimum_affinity_policy=MinimumAffinityPolicy(90, "fail"))
        )
    names = [step.step for step in result.steps]
    assert names.count("network") == 1
    assert (
        next(step for step in result.steps if step.step == "network").status
        == "error"
    )


def test_provision_lpar_propagates_unexpected_step_failure(monkeypatch, mock_hmc):
    """Programming defects are not disguised as ordinary partial results."""
    _hmc_env(monkeypatch)
    _mock_preconditions(mock_hmc)
    _mock_execution_steps(mock_hmc)

    with patch(
        "hmc_mcp.client.HMCClient.add_vscsi_adapter",
        new=AsyncMock(side_effect=TypeError("adapter defect")),
    ):
        with pytest.raises(TypeError, match="adapter defect"):
            hmc_provision_lpar(**_provision_args())


def test_provision_lpar_reports_created_resource_without_uuid(monkeypatch, mock_hmc):
    """A successful create with no response body is not reported as no creation."""
    _hmc_env(monkeypatch)
    _mock_preconditions(mock_hmc)
    mock_hmc.put(f"/rest/api/uom/ManagedSystem/{SYSTEM_UUID}/LogicalPartition").mock(
        return_value=httpx.Response(201)
    )

    result = hmc_provision_lpar(**_provision_args())

    assert result.resource_created is True
    assert result.workflow_completed is False
    assert result.lpar_uuid is None
    assert result.steps[0].status == "error"
    assert "no UUID" in result.steps[0].result
    assert result.ownership_stamped is None
    assert "no LPAR body" in result.warnings[0]


def test_provision_lpar_dry_run_issues_no_mutating_request(monkeypatch, mock_hmc):
    """R18: proven at the transport, not per registered route.

    `test_provision_lpar_dry_run_validates_only` above asserts one route it
    registered was not taken. That stays green if the handler mutates through a
    route the test never named, which is the failure mode epic #218 requirement 5
    means by "provably performs no mutation … rather than inferred".

    Here every request the call actually made is read back, so any write on this
    path fails whatever URL it targets.
    """
    _hmc_env(monkeypatch)
    _mock_preconditions(mock_hmc)

    result = hmc_provision_lpar(**_provision_args(dry_run=True))

    assert result.dry_run is True
    requests = assert_no_mutating_requests(mock_hmc)
    # Reads do happen, and are the classification: the name-uniqueness, VLAN, and
    # volume-group precondition checks, plus the system resolve. Asserted
    # non-zero so a future change that skips the handler entirely — and therefore
    # trivially "mutates nothing" — cannot pass this test.
    assert requests > 0


def test_provision_invalid_caller_token_fails_before_preconditions(
    monkeypatch, mock_hmc
):
    """dry_run=True still fails fast on a bad token (spec guarantee 3)."""
    _hmc_env(monkeypatch)
    with pytest.raises(ValueError, match="caller_token"):
        hmc_provision_lpar(
            **_provision_args(name="p-lpar", dry_run=True, caller_token="a=b")
        )


def test_provision_operation_rejects_bad_token_before_any_round_trip(monkeypatch):
    """Direct provision_lpar callers bypass hmc_provision_lpar's entry check,
    so the operation validates first, before any HMC round trip."""
    _hmc_env(monkeypatch)
    from hmc_mcp.operations.provision import provision_lpar

    with pytest.raises(ValueError, match="caller_token"):
        asyncio.run(
            provision_lpar(None, **_provision_args(caller_token="a=b"))  # type: ignore[arg-type]
        )


def test_provision_passes_caller_token_to_creation(monkeypatch, mock_hmc):
    _hmc_env(monkeypatch)
    _mock_preconditions(mock_hmc, name="p-lpar")
    _mock_execution_steps(mock_hmc)
    result = hmc_provision_lpar(
        **_provision_args(name="p-lpar", power_on=False, caller_token="CHG-9")
    )
    assert result.resource_created is True
    assert result.ownership_stamped is True


def test_provision_policy_rejects_unsupported_system_before_mutation(
    monkeypatch, mock_hmc
):
    _hmc_env(monkeypatch)
    with (
        patch(
            "hmc_mcp.operations.provision.resolve_ssh_names",
            AsyncMock(return_value=("system", None)),
        ),
        patch(
            "hmc_mcp.operations.provision.require_minimum_affinity_policy_capability",
            AsyncMock(side_effect=HMCCLIError("POWER11 required")),
        ),
    ):
        with pytest.raises(HMCCLIError, match="POWER11"):
            hmc_provision_lpar(
                **_provision_args(
                    minimum_affinity_policy=MinimumAffinityPolicy(80, "warn")
                )
            )
    assert_no_mutating_requests(mock_hmc)


def test_provision_applies_explicit_fail_policy_before_network(monkeypatch, mock_hmc):
    _hmc_env(monkeypatch)
    _mock_preconditions(mock_hmc)
    _mock_execution_steps(mock_hmc)
    policy = MinimumAffinityPolicy(90, "fail")
    setter = AsyncMock(return_value="changed")
    with (
        patch(
            "hmc_mcp.operations.provision.resolve_ssh_names",
            AsyncMock(return_value=("system", None)),
        ),
        patch(
            "hmc_mcp.operations.provision.require_minimum_affinity_policy_capability",
            AsyncMock(),
        ),
        patch("hmc_mcp.operations.provision.set_minimum_affinity_policy", setter),
    ):
        result = hmc_provision_lpar(
            **_provision_args(minimum_affinity_policy=policy, power_on=False)
        )
    assert result.workflow_completed is True
    assert [step.step for step in result.steps][:3] == [
        "create",
        "minimum_affinity_policy",
        "network",
    ]
    setter.assert_awaited_once_with(ANY, SYSTEM_UUID, "web01", policy)
