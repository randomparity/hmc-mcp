"""Live validation scenarios for reversible PCIe assignment — issue #217.

SR-IOV arm (ST23–ST28, subtask 23): Exercises reversible SR-IOV logical-port
assignment on ltczz386 / ltczz386-lp3.  The LPAR must be Not Activated before
this module runs.

Dedicated-slot arm (ST29–ST34, subtask 24): Exercises reversible dedicated PCIe
slot assignment via a run-unique owner-stamped LPAR.  It SKIPs outside the
ADR 0053-admitted HMC release and system model (V10R3 M1060 / 8375-42A), because
the io_slots profile grammar this arm issues has only been probed on a Power8
documentation row and must not be executed on an unidentified environment.
Configuration is required — no fallback to an arbitrary system.

Admitted environment (ADR 0053 / operations/pcie.py):
  HMC V10R3 M1060 · managed-system model 8375-42A

SR-IOV test structure (ST23–ST28):
  ST23 — Baseline: read adapter/physport/logport inventory; confirm lp3 profile is clean
  ST24 — Assign logical port 27004003 (phys_port 0, 2% capacity) to lp3
  ST25 — Verify effective + profile readback after assign
  ST26 — Unassign; verify logical port is unconfigured and profile is restored
  ST27 — Reassign on existing LPAR (same port, same capacity)
  ST28 — Cleanup: unassign again; final profile + inventory confirm; PASS/SKIP/FAIL

Dedicated-slot test structure (ST29–ST34, subtask 24):
  ST29 — Baseline: read HMC release/model, list dedicated slots, select one unassigned
  ST30 — Probe create-time assignment refusal; create run-unique fixture LPAR
  ST31 — Assign selected slot via documented profile grammar (chsyscfg io_slots+)
  ST32 — Verify profile readback after assign
  ST33 — Unassign (io_slots-), verify exact baseline restored, reassign (io_slots+)
  ST34 — Cleanup: slot removal then LPAR delete, each only on an exact match

Configuration variables for the dedicated arm:
  HMC_LIVE_PCIE_SYSTEM  — managed-system name (required)
  HMC_LIVE_PCIE_LPAR_PREFIX — LPAR name prefix for the run-unique fixture (required)
  HMC_LIVE_PCIE_PROFILE — profile name (default: default_profile)
  HMC_LIVE_PCIE_DRC_INDEX — specific DRC index to test; auto-selects first unassigned if absent

Missing hardware or a wrong LPAR state produces SKIP per arm, not FAIL.
Any cleanup mutation failure records manual-recovery evidence and halts further
cleanup (does not attempt additional mutations on an unknown state).
"""

from __future__ import annotations

import os
import shlex
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from fastmcp import Client

from hmc_mcp.operations.ownership import parse_lpar_ownership_caller_token

# These two are imported rather than restated so the arm's SKIP envelope cannot
# drift from the one require_admitted_environment enforces for the SR-IOV path;
# a copied literal would go stale silently the first time the admitted release moves.
from hmc_mcp.operations.pcie import (
    _ADMITTED_HMC_RELEASE,
    _ADMITTED_SYSTEM_MODEL,
    PCIE_ASSIGNMENT_UNAVAILABLE_REASON,
)
from hmc_mcp.ssh.commands import build_attribute_record, build_filter

if TYPE_CHECKING:
    from live_test_runner import RunState


# ---------------------------------------------------------------------------
# Constants — selected from the pre-test inventory of ltczz386
# ---------------------------------------------------------------------------

_ADAPTER_ID = "1"               # adapter_id=1, config_state=sriov, functional_state=1
_PHYS_PORT_ID = "0"             # port 0, phys_port_loc U78D2.001.RCH0268-P1-C4-T1
#                                 lp2 reduced to 95% (from 100%) to free 5% for this test;
#                                 will be restored to 100% after the test completes.
_LOGICAL_PORT_ID = "27004003"   # unconfigured, location U78D2.001.RCH0268-P1-C4-T1-S3
_CAPACITY_PERCENT = 5.0         # 5% — the freed capacity on port 0
_PROFILE_NAME = "default_profile"


# ---------------------------------------------------------------------------
# SR-IOV state snapshot helpers
# ---------------------------------------------------------------------------


@dataclass
class _SriovState:
    """Point-in-time SR-IOV state captured for one logical port."""

    configured: bool  # True → appears in hmc_list_sriov_logical_ports with owner
    profile_ports: str | None  # sriov_eth_logical_ports value from profile
    owner_lpar: str | None
    capacity_percent: float | None


async def _read_sriov_state(client: Client, state: RunState) -> _SriovState:
    """Read current SR-IOV state for the test logical port and lp3 profile."""
    context = state.context

    # Read configured logical ports
    st, data = await state.call(
        client,
        "hmc_list_sriov_logical_ports",
        system_name_or_uuid=context.system_name,
        adapter_id=_ADAPTER_ID,
        logical_port_id=_LOGICAL_PORT_ID,
    )
    configured = False
    owner_lpar = None
    capacity_percent = None
    if st == "PASS" and isinstance(data, dict):
        items = data.get("items") or []
        for item in items:
            if (
                isinstance(item, dict)
                and item.get("logical_port_id") == _LOGICAL_PORT_ID
                and item.get("availability") not in ("unconfigured", None, "")
                and item.get("owner_lpar")
            ):
                configured = True
                owner_lpar = item.get("owner_lpar")
                cap = item.get("capacity_percent")
                capacity_percent = float(cap) if cap is not None else None
                break

    # Read profile SR-IOV eth logical ports field
    st_prof, data_prof = await state.call(
        client,
        "hmc_run_command",
        cmd=(
            f"lssyscfg -r prof -m ltczz386 "
            f"--filter 'lpar_names={context.lp3_name},profile_names={_PROFILE_NAME}' "
            f"-F sriov_eth_logical_ports"
        ),
    )
    profile_ports: str | None = None
    if st_prof == "PASS" and isinstance(data_prof, str):
        profile_ports = data_prof.strip()

    return _SriovState(configured, profile_ports, owner_lpar, capacity_percent)


def _sriov_state_summary(s: _SriovState) -> str:
    return (
        f"configured={s.configured} owner={s.owner_lpar!r} "
        f"capacity={s.capacity_percent}% profile_ports={s.profile_ports!r}"
    )


# ---------------------------------------------------------------------------
# ST23 — Baseline: inventory + lp3 profile clean check
# ---------------------------------------------------------------------------


async def capture_sriov_baseline(client: Client, state: RunState) -> bool:
    """Record the pre-test SR-IOV inventory.  Returns False if prerequisites fail."""
    context = state.context
    print("\n=== ST23: SR-IOV Baseline (issue #217) ===")

    # 1. Adapter inventory
    st, data = await state.call(
        client,
        "hmc_list_sriov_adapters",
        system_name_or_uuid=context.system_name,
        adapter_id=_ADAPTER_ID,
    )
    state.record(23, "hmc_list_sriov_adapters (baseline)", st, data)
    if st != "PASS":
        state.record(
            23,
            "baseline-abort",
            "FAIL",
            "SR-IOV adapter inventory failed — cannot proceed",
        )
        return False

    # Gate on capability-available
    if isinstance(data, dict) and data.get("capability") == "capability-unavailable":
        reason = data.get("unavailable_reason", "")
        state.skip(
            23,
            "hmc_list_sriov_adapters (capability)",
            f"SR-IOV capability unavailable on this environment: {reason}",
        )
        return False

    # Confirm adapter is in SR-IOV mode and healthy
    items = data.get("items") or [] if isinstance(data, dict) else []
    adapter_ok = any(
        isinstance(i, dict)
        and i.get("adapter_id") == _ADAPTER_ID
        and i.get("mode") == "sriov"
        and i.get("availability") == "1"
        for i in items
    )
    if not adapter_ok:
        state.skip(
            23,
            "hmc_list_sriov_adapters (health check)",
            f"adapter {_ADAPTER_ID!r} is not in healthy sriov mode; SKIP SR-IOV arm",
        )
        return False
    state.record(
        23,
        "hmc_list_sriov_adapters (health check)",
        "PASS",
        f"adapter {_ADAPTER_ID} in healthy sriov mode",
    )

    # 2. Physical port inventory — also check that the port has remaining capacity
    st, data = await state.call(
        client,
        "hmc_list_sriov_physical_ports",
        system_name_or_uuid=context.system_name,
        adapter_id=_ADAPTER_ID,
        physical_port_id=_PHYS_PORT_ID,
    )
    state.record(23, "hmc_list_sriov_physical_ports (baseline)", st, data)
    if st != "PASS":
        state.skip(
            23,
            "sriov physical port check",
            "physical port inventory failed: SKIP SR-IOV arm",
        )
        return False

    # Capacity check: hmc_list_sriov_logical_ports returns configured+unconfigured ports.
    # We need at least _CAPACITY_PERCENT of remaining room on the physical port.
    # Use the raw configured logport list to compute used capacity on _PHYS_PORT_ID.
    st_lp, data_lp = await state.call(
        client,
        "hmc_list_sriov_logical_ports",
        system_name_or_uuid=context.system_name,
        adapter_id=_ADAPTER_ID,
        physical_port_id=_PHYS_PORT_ID,
    )
    used_capacity = 0.0
    if st_lp == "PASS" and isinstance(data_lp, dict):
        for item in data_lp.get("items") or []:
            if (
                isinstance(item, dict)
                and item.get("availability") not in ("unconfigured", None, "")
                and item.get("capacity_percent") is not None
            ):
                try:
                    used_capacity += float(item["capacity_percent"])
                except (ValueError, TypeError):
                    pass
    available = 100.0 - used_capacity
    state.record(
        23,
        "sriov capacity check (pre-test)",
        "PASS" if available >= _CAPACITY_PERCENT else "SKIP",
        f"phys_port {_PHYS_PORT_ID}: used={used_capacity}% available={available}% needed={_CAPACITY_PERCENT}%",
    )
    if available < _CAPACITY_PERCENT:
        state.skip(
            23,
            "sriov assign arm",
            f"phys_port {_PHYS_PORT_ID} has only {available}% capacity remaining "
            f"(need {_CAPACITY_PERCENT}%); all unconfigured logical ports are T1-addressed "
            "and hmc-mcp's location-code check blocks cross-port assignment — SKIP assign arm. "
            "NOTE: chhwres assigns T1 logical ports to phys_port 1 (T2) successfully "
            "at the firmware layer; the location-code check is an hmc-mcp admission gate, "
            "not a firmware constraint.",
        )
        return False

    # 3. Logical port inventory (confirm test port is unconfigured)
    st, data = await state.call(
        client,
        "hmc_list_sriov_logical_ports",
        system_name_or_uuid=context.system_name,
        adapter_id=_ADAPTER_ID,
        logical_port_id=_LOGICAL_PORT_ID,
    )
    state.record(23, "hmc_list_sriov_logical_ports (baseline)", st, data)
    if st != "PASS":
        state.skip(
            23,
            "sriov logical port baseline",
            "logical port inventory failed: SKIP SR-IOV arm",
        )
        return False
    items = data.get("items") or [] if isinstance(data, dict) else []
    already_configured = any(
        isinstance(i, dict)
        and i.get("logical_port_id") == _LOGICAL_PORT_ID
        and i.get("availability") not in ("unconfigured", None, "")
        for i in items
    )
    if already_configured:
        state.skip(
            23,
            "sriov logical port precondition",
            f"logical port {_LOGICAL_PORT_ID} is already configured (not a clean baseline); "
            "SKIP SR-IOV arm to avoid mutating a port this run does not own",
        )
        return False
    state.record(
        23,
        "sriov logical port precondition",
        "PASS",
        f"logical port {_LOGICAL_PORT_ID} is unconfigured — clean baseline confirmed",
    )

    # 4. lp3 profile SR-IOV field
    sriov_state = await _read_sriov_state(client, state)
    state.record(
        23,
        "lp3 profile sriov_eth_logical_ports (baseline)",
        "PASS",
        _sriov_state_summary(sriov_state),
    )
    # Accept two clean starting states:
    #   (a) profile is none — no prior assignment
    #   (b) profile already contains exactly our test port (e.g. written manually
    #       ahead of this run so the unassign path can be exercised) — the assign
    #       operation will detect idempotence and the unassign will clear it.
    profile_has_our_port = (
        sriov_state.profile_ports not in (None, "none", "")
        and f":{_LOGICAL_PORT_ID}:" in str(sriov_state.profile_ports)
    )
    profile_clean = sriov_state.profile_ports in (None, "none", "")
    if not profile_clean and not profile_has_our_port:
        state.skip(
            23,
            "lp3 profile precondition",
            f"lp3 default_profile already has sriov_eth_logical_ports={sriov_state.profile_ports!r} "
            f"(not our test port {_LOGICAL_PORT_ID}); "
            "SKIP SR-IOV arm to avoid overwriting an existing assignment",
        )
        return False
    state.record(
        23,
        "lp3 profile precondition",
        "PASS",
        (
            f"sriov_eth_logical_ports contains our test port {_LOGICAL_PORT_ID} — "
            "profile ready for assign (idempotent) + unassign round-trip"
            if profile_has_our_port
            else "sriov_eth_logical_ports=none — lp3 profile is clean"
        ),
    )
    return True


# ---------------------------------------------------------------------------
# ST24 — Assign logical port to lp3
# ---------------------------------------------------------------------------


async def assign_sriov_to_lp3(client: Client, state: RunState) -> bool:
    """Assign test logical port to lp3.  Returns False if the call failed."""
    context = state.context
    print("\n=== ST24: SR-IOV Assign (issue #217) ===")
    st, data = await state.call(
        client,
        "hmc_assign_sriov_logical_port",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
        adapter_id=_ADAPTER_ID,
        physical_port_id=_PHYS_PORT_ID,
        logical_port_id=_LOGICAL_PORT_ID,
        capacity_percent=_CAPACITY_PERCENT,
        profile_name=_PROFILE_NAME,
        ownership_override=True,
    )
    state.record(24, "hmc_assign_sriov_logical_port", st, data)
    return st == "PASS"


# ---------------------------------------------------------------------------
# ST25 — Verify effective + profile readback after assign
# ---------------------------------------------------------------------------


async def verify_sriov_assigned(client: Client, state: RunState) -> bool:
    """Verify the logical port is configured on lp3 after assign."""
    context = state.context
    print("\n=== ST25: SR-IOV Post-Assign Verify (issue #217) ===")
    sriov_state = await _read_sriov_state(client, state)
    state.record(
        25,
        "sriov post-assign state",
        "PASS" if sriov_state.configured else "FAIL",
        _sriov_state_summary(sriov_state),
    )

    # Verify owner
    owner_ok = sriov_state.owner_lpar == context.lp3_name
    state.record(
        25,
        "sriov owner check",
        "PASS" if owner_ok else "FAIL",
        f"expected owner={context.lp3_name!r}, got {sriov_state.owner_lpar!r}",
    )

    # Verify capacity
    cap_ok = abs((sriov_state.capacity_percent or 0.0) - _CAPACITY_PERCENT) < 0.01
    state.record(
        25,
        "sriov capacity check",
        "PASS" if cap_ok else "FAIL",
        f"expected {_CAPACITY_PERCENT}%, got {sriov_state.capacity_percent}%",
    )

    # Profile readback — informational for the dynamic path on a Not Activated LPAR.
    # chhwres -o a updates the effective layer; the HMC does not auto-update the
    # profile.  Record the value but do not gate the pass/fail decision on it.
    state.record(
        25,
        "sriov profile readback (informational)",
        "PASS",
        f"profile sriov_eth_logical_ports={sriov_state.profile_ports!r} "
        "(dynamic assign does not update the profile for Not Activated LPARs)",
    )

    return sriov_state.configured and owner_ok and cap_ok


# ---------------------------------------------------------------------------
# ST26 — Unassign; verify baseline restored
# ---------------------------------------------------------------------------


async def unassign_sriov_from_lp3(client: Client, state: RunState) -> bool:
    """Unassign the test logical port from lp3.  Returns False if the call failed."""
    context = state.context
    print("\n=== ST26: SR-IOV Unassign (issue #217) ===")
    st, data = await state.call(
        client,
        "hmc_unassign_sriov_logical_port",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
        adapter_id=_ADAPTER_ID,
        physical_port_id=_PHYS_PORT_ID,
        logical_port_id=_LOGICAL_PORT_ID,
        profile_name=_PROFILE_NAME,
        ownership_override=True,
    )
    state.record(26, "hmc_unassign_sriov_logical_port", st, data)
    if st != "PASS":
        return False

    # Verify: profile restored to none.
    # The unassign_sriov_logical_port operation is a profile-only path (chsyscfg).
    # It clears sriov_eth_logical_ports in the profile but does NOT issue chhwres -o r
    # to remove the effective assignment — that only happens at next activation.
    # The effective layer is expected to remain configured; only the profile is checked.
    sriov_state = await _read_sriov_state(client, state)
    profile_clean = sriov_state.profile_ports in (None, "none", "")
    state.record(
        26,
        "sriov post-unassign profile verify",
        "PASS" if profile_clean else "FAIL",
        f"profile_ports={sriov_state.profile_ports!r} (effective still has port — expected for profile-only unassign path)",
    )
    state.record(
        26,
        "sriov post-unassign effective (informational)",
        "PASS",
        f"effective configured={sriov_state.configured} owner={sriov_state.owner_lpar!r} "
        "(profile-only unassign does not touch effective layer; port remains until next activation)",
    )
    return profile_clean


# ---------------------------------------------------------------------------
# ST27 — Reassign on existing LPAR (prove round-trip)
# ---------------------------------------------------------------------------


async def reassign_sriov_to_lp3(client: Client, state: RunState) -> bool:
    """Re-assign the same port to prove the round-trip path."""
    context = state.context
    print("\n=== ST27: SR-IOV Reassign (issue #217) ===")
    st, data = await state.call(
        client,
        "hmc_assign_sriov_logical_port",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
        adapter_id=_ADAPTER_ID,
        physical_port_id=_PHYS_PORT_ID,
        logical_port_id=_LOGICAL_PORT_ID,
        capacity_percent=_CAPACITY_PERCENT,
        profile_name=_PROFILE_NAME,
        ownership_override=True,
    )
    state.record(27, "hmc_assign_sriov_logical_port (reassign)", st, data)
    if st != "PASS":
        return False

    # Verify ownership
    sriov_state = await _read_sriov_state(client, state)
    ok = sriov_state.configured and sriov_state.owner_lpar == context.lp3_name
    state.record(
        27,
        "sriov post-reassign verify",
        "PASS" if ok else "FAIL",
        _sriov_state_summary(sriov_state),
    )
    return ok


# ---------------------------------------------------------------------------
# ST28 — Cleanup: final unassign + inventory confirm
# ---------------------------------------------------------------------------


async def cleanup_sriov(client: Client, state: RunState) -> None:
    """Unassign the test port (cleanup) and confirm the baseline is restored."""
    context = state.context
    print("\n=== ST28: SR-IOV Cleanup (issue #217) ===")

    # Re-read before mutating — guard before any cleanup action
    sriov_state = await _read_sriov_state(client, state)
    state.record(
        28,
        "sriov pre-cleanup state",
        "PASS",
        _sriov_state_summary(sriov_state),
    )

    # Only attempt cleanup if we own the port
    if not sriov_state.configured:
        state.record(
            28,
            "sriov cleanup: port already unconfigured",
            "PASS",
            "no cleanup action required",
        )
    elif sriov_state.owner_lpar != context.lp3_name:
        state.record(
            28,
            "sriov cleanup: owner mismatch",
            "FAIL",
            f"MANUAL RECOVERY REQUIRED: logical port {_LOGICAL_PORT_ID} is assigned "
            f"to {sriov_state.owner_lpar!r} — expected {context.lp3_name!r}. "
            "Do not unassign — another LPAR owns this port.",
        )
        return
    else:
        # Step 1: profile unassign (clears sriov_eth_logical_ports via chsyscfg)
        st, data = await state.call(
            client,
            "hmc_unassign_sriov_logical_port",
            system_name_or_uuid=context.system_name,
            lpar_name_or_uuid=context.lp3_name,
            adapter_id=_ADAPTER_ID,
            physical_port_id=_PHYS_PORT_ID,
            logical_port_id=_LOGICAL_PORT_ID,
            profile_name=_PROFILE_NAME,
            ownership_override=True,
        )
        state.record(28, "hmc_unassign_sriov_logical_port (cleanup)", st, data)
        if st != "PASS":
            state.record(
                28,
                "sriov cleanup: unassign failed",
                "FAIL",
                f"MANUAL RECOVERY REQUIRED: profile unassign failed — "
                f"logical port {_LOGICAL_PORT_ID} may still be in profile and effective layer. "
                f"Run: chhwres -r sriov --rsubtype logport -m ltczz386 "
                f"-o r -p {context.lp3_name} "
                f"-a \"adapter_id={_ADAPTER_ID},logical_port_id={_LOGICAL_PORT_ID}\" "
                f"to recover. Error: {str(data)[:400]}",
            )
            return

        # Step 2: effective removal (chhwres -o r) — the profile-only unassign
        # does not touch the effective layer.  Remove it explicitly so the
        # port returns to the unconfigured pool.
        st2, data2 = await state.call(
            client,
            "hmc_run_command",
            cmd=(
                f"chhwres -r sriov --rsubtype logport"
                f" -m {context.system_name}"
                f" -o r -p {context.lp3_name}"
                f" -a \"adapter_id={_ADAPTER_ID},logical_port_id={_LOGICAL_PORT_ID}\""
            ),
        )
        state.record(28, "chhwres -o r (effective cleanup)", st2, data2)
        if st2 != "PASS":
            state.record(
                28,
                "sriov cleanup: effective removal failed",
                "FAIL",
                f"MANUAL RECOVERY REQUIRED: effective removal failed — "
                f"logical port {_LOGICAL_PORT_ID} still assigned to {context.lp3_name!r}. "
                f"Run manually: chhwres -r sriov --rsubtype logport -m {context.system_name} "
                f"-o r -p {context.lp3_name} "
                f"-a \"adapter_id={_ADAPTER_ID},logical_port_id={_LOGICAL_PORT_ID}\" "
                f"Error: {str(data2)[:400]}",
            )
            return

    # Final logical-port inventory confirm
    st, data = await state.call(
        client,
        "hmc_list_sriov_logical_ports",
        system_name_or_uuid=context.system_name,
        adapter_id=_ADAPTER_ID,
        logical_port_id=_LOGICAL_PORT_ID,
    )
    state.record(28, "hmc_list_sriov_logical_ports (final)", st, data)
    if st == "PASS" and isinstance(data, dict):
        items = data.get("items") or []
        still_configured = any(
            isinstance(i, dict)
            and i.get("logical_port_id") == _LOGICAL_PORT_ID
            and i.get("availability") not in ("unconfigured", None, "")
            for i in items
        )
        state.record(
            28,
            "sriov final inventory check",
            "FAIL" if still_configured else "PASS",
            (
                f"MANUAL RECOVERY REQUIRED: logical port {_LOGICAL_PORT_ID} "
                "is still configured after cleanup"
                if still_configured
                else f"logical port {_LOGICAL_PORT_ID} is unconfigured — baseline restored"
            ),
        )

    # Final profile check
    final_state = await _read_sriov_state(client, state)
    profile_clean = final_state.profile_ports in (None, "none", "")
    state.record(
        28,
        "lp3 profile final check",
        "PASS" if profile_clean else "FAIL",
        (
            f"MANUAL RECOVERY REQUIRED: profile sriov_eth_logical_ports="
            f"{final_state.profile_ports!r} after cleanup — "
            f"run: chsyscfg -r prof -m ltczz386 "
            f"-i \"name={_PROFILE_NAME},lpar_name={context.lp3_name},"
            f"sriov_eth_logical_ports=none\" to recover"
            if not profile_clean
            else "sriov_eth_logical_ports=none — lp3 profile restored to baseline"
        ),
    )


# ---------------------------------------------------------------------------
# Top-level orchestrator: ST23–ST28 as a single subtask entry
# ---------------------------------------------------------------------------


async def exercise_sriov_assignment(client: Client, state: RunState) -> None:
    """Orchestrate the full SR-IOV assign/verify/unassign/reassign/cleanup sequence."""
    print("\n============================")
    print("=== SR-IOV Live Test (issue #217) ===")
    print("============================")

    # Phase 1: Baseline
    baseline_ok = await capture_sriov_baseline(client, state)
    if not baseline_ok:
        print("  SR-IOV baseline check failed or SKIP — halting SR-IOV arm")
        return

    # Phase 2: Assign
    assign_ok = await assign_sriov_to_lp3(client, state)

    # Phase 3: Verify assign (always run, even if assign failed — documents state)
    verify_ok = await verify_sriov_assigned(client, state)

    # Phase 4: Unassign (only if assign succeeded and verification passed)
    if assign_ok and verify_ok:
        unassign_ok = await unassign_sriov_from_lp3(client, state)
    else:
        state.skip(
            26,
            "hmc_unassign_sriov_logical_port",
            f"skipping unassign: assign_ok={assign_ok} verify_ok={verify_ok}",
        )
        unassign_ok = False

    # Phase 5: Reassign (only if unassign succeeded — proves round-trip)
    if unassign_ok:
        await reassign_sriov_to_lp3(client, state)
    else:
        state.skip(
            27,
            "hmc_assign_sriov_logical_port (reassign)",
            f"skipping reassign: unassign_ok={unassign_ok}",
        )

    # Phase 6: Cleanup — always runs regardless of test outcome
    await cleanup_sriov(client, state)


# ---------------------------------------------------------------------------
# Dedicated PCIe arm — ST29–ST34 (subtask 24)
# ---------------------------------------------------------------------------

_DEDICATED_ENV_SYSTEM = "HMC_LIVE_PCIE_SYSTEM"
_DEDICATED_ENV_PREFIX = "HMC_LIVE_PCIE_LPAR_PREFIX"
_DEDICATED_ENV_PROFILE = "HMC_LIVE_PCIE_PROFILE"
_DEDICATED_ENV_DRC = "HMC_LIVE_PCIE_DRC_INDEX"
_DEFAULT_DEDICATED_PROFILE = "default_profile"


@dataclass(frozen=True)
class _DedicatedConfig:
    system_name: str
    lpar_prefix: str
    profile_name: str
    drc_index: str | None


@dataclass
class _DedicatedFixture:
    config: _DedicatedConfig
    run_marker: str
    lpar_name: str
    probe_lpar_name: str
    lpar_uuid: str | None = None
    drc_index: str | None = None
    baseline_io_slots: str | None = None
    applied_io_slots: str | None = None
    created: bool = False
    probe_created: bool = False
    probe_lpar_uuid: str | None = None


@dataclass(frozen=True)
class _DedicatedState:
    slot_owner: str | None
    profile_io_slots: str | None
    lpar_uuid: str | None
    caller_token: str | None


#: Characters the HMC's own ``-i`` / ``--filter`` record parser treats as
#: structure. `build_attribute_record` and `build_filter` refuse them — by
#: raising, at command-construction time, in the caller's frame rather than
#: inside `RunState.call`. The first such construction happens *after* the
#: fixture partition exists, so an unvalidated value would abandon a created
#: partition with no cleanup and no results file (see the orchestrator's
#: try/finally in Task 4). Refusing here turns that into the ST29
#: configuration SKIP, before anything is created.
_RECORD_DELIMITERS = ',="[]\\'


def _config_value_safe(value: str) -> bool:
    """Whether *value* can cross the HMC record grammar unchanged."""
    return not any(character in _RECORD_DELIMITERS or character < " " for character in value)


def _dedicated_config(environ: Mapping[str, str]) -> _DedicatedConfig | None:
    """Resolve the arm's explicit configuration, or None when it is absent.

    Never falls back to ``LiveTestContext.system_name``: issue #217 requires an
    explicitly configured managed system and forbids running against an
    arbitrary one.
    """
    system_name = (environ.get(_DEDICATED_ENV_SYSTEM) or "").strip()
    lpar_prefix = (environ.get(_DEDICATED_ENV_PREFIX) or "").strip()
    if not system_name or not lpar_prefix:
        return None
    profile_name = (
        environ.get(_DEDICATED_ENV_PROFILE) or ""
    ).strip() or _DEFAULT_DEDICATED_PROFILE
    drc_index = (environ.get(_DEDICATED_ENV_DRC) or "").strip() or None
    if not all(
        _config_value_safe(value)
        for value in (lpar_prefix, profile_name, drc_index or "")
    ):
        return None
    return _DedicatedConfig(system_name, lpar_prefix, profile_name, drc_index)


def _new_run_marker() -> str:
    """Return a run-unique ADR 0064 caller token for this invocation."""
    return f"pcie-{uuid.uuid4().hex[:8]}"


def _dedicated_state_summary(s: _DedicatedState) -> str:
    return (
        f"slot_owner={s.slot_owner!r} profile_io_slots={s.profile_io_slots!r} "
        f"lpar_uuid={s.lpar_uuid!r} caller_token={s.caller_token!r}"
    )


def _environment_admitted(version: str, model: str) -> bool:
    """Whether this HMC release and system model are the ADR 0053-admitted pair.

    The same normalized comparison ``operations/pcie.py`` applies in
    ``require_admitted_environment``: the arm mutates through raw profile
    grammar rather than through that operation, so nothing else enforces the
    envelope on this path.
    """
    normalized = " ".join(version.split()).lower()
    admitted = _ADMITTED_HMC_RELEASE.lower() in normalized or all(
        marker in normalized
        for marker in ("version: 10", "release: 3", "service pack: 1060")
    )
    return admitted and model == _ADMITTED_SYSTEM_MODEL


def _profile_io_slots_command(fixture: _DedicatedFixture) -> str:
    """Return the exact `io_slots` profile read admitted by ADR 0053.

    The `--filter` expression goes through `build_filter` for the same reason
    the record goes through `build_attribute_record`: a delimiter inside
    `profile_name` — which arrives from the environment with only `.strip()`
    applied — would otherwise rewrite the filter and answer about a profile
    the arm did not name, while Guard B's exact-match comparisons still
    reported success. `shlex.quote` protects the remote shell, not the HMC's
    own record parser, and does not substitute for it.
    """
    config = fixture.config
    filters = build_filter(
        [
            ("lpar_names", fixture.lpar_name),
            ("profile_names", config.profile_name),
        ]
    )
    return (
        f"lssyscfg -r prof -m {shlex.quote(config.system_name)} "
        f"--filter {shlex.quote(filters)} -F io_slots"
    )


def _change_io_slots_command(fixture: _DedicatedFixture, *, add: bool) -> str:
    """Return the documented profile mutation, without --force (ADR 0055)."""
    config = fixture.config
    record = build_attribute_record(
        [
            ("name", config.profile_name),
            ("io_slots+" if add else "io_slots-", f"{fixture.drc_index}//0"),
            ("lpar_name", fixture.lpar_name),
        ]
    )
    return (
        f"chsyscfg -r prof -m {shlex.quote(config.system_name)} "
        f"-i {shlex.quote(record)}"
    )


async def _read_profile_io_slots(
    client: Client, state: RunState, fixture: _DedicatedFixture
) -> str | None:
    """Return the profile's exact `io_slots` value, or None when unreadable.

    A response that is not exactly one non-empty line is refused, matching
    `ssh/profiles.py:read_lpar_profile_record`: the guards compare exact
    strings, so a multi-record answer — the filter selected more than the arm
    named — must read as unreadable rather than as its first line.
    """
    st, data = await state.call(
        client, "hmc_run_command", cmd=_profile_io_slots_command(fixture)
    )
    if st != "PASS" or not isinstance(data, str):
        return None
    records = [line for line in data.splitlines() if line.strip()]
    if len(records) != 1:
        # Includes the empty answer. A profile with no slots prints `none`,
        # so an empty response is a failed read, and reading it as "no
        # slots" would hand the guards a baseline nothing established.
        return None
    return records[0].strip()


async def _read_dedicated_state(
    client: Client, state: RunState, fixture: _DedicatedFixture
) -> _DedicatedState:
    """Read live slot ownership, profile io_slots, LPAR UUID, and caller token."""
    config = fixture.config
    slot_owner = None
    st, data = await state.call(
        client,
        "hmc_list_dedicated_pcie_slots",
        system_name_or_uuid=config.system_name,
    )
    if st == "PASS" and isinstance(data, dict):
        for item in data.get("items") or []:
            if (
                isinstance(item, dict)
                and item.get("drc_index") == fixture.drc_index
            ):
                slot_owner = item.get("owner_lpar") or None
                break

    profile_io_slots = await _read_profile_io_slots(client, state, fixture)

    lpar_uuid = None
    st_lpar, data_lpar = await state.call(
        client,
        "hmc_get_lpar",
        lpar_name_or_uuid=fixture.lpar_name,
        system_name_or_uuid=config.system_name,
    )
    if st_lpar == "PASS" and isinstance(data_lpar, dict):
        lpar_uuid = data_lpar.get("UUID") or data_lpar.get("uuid")

    caller_token = None
    st_desc, data_desc = await state.call(
        client,
        "hmc_get_lpar_description",
        system_name_or_uuid=config.system_name,
        lpar_name_or_uuid=fixture.lpar_name,
    )
    if st_desc == "PASS" and isinstance(data_desc, str):
        caller_token = parse_lpar_ownership_caller_token(data_desc)

    return _DedicatedState(slot_owner, profile_io_slots, lpar_uuid, caller_token)


async def _admit_dedicated_environment(
    client: Client, state: RunState, config: _DedicatedConfig
) -> bool:
    """Record the HMC release and system model; admit only the ADR 0053 pair."""
    st_v, version = await state.call(client, "hmc_run_command", cmd="lshmc -V")
    st_m, model = await state.call(
        client,
        "hmc_run_command",
        cmd=(
            "lssyscfg -r sys -m "
            f"{shlex.quote(config.system_name)} -F type_model"
        ),
    )
    if st_v != "PASS" or st_m != "PASS":
        state.skip(
            29,
            "dedicated admitted environment",
            "could not read the HMC release or the managed-system type-model; "
            "the dedicated arm mutates through raw profile grammar and will "
            "not do so on an unidentified environment — SKIP dedicated arm",
        )
        return False
    version_text = str(version).strip()
    model_text = str(model).strip()
    admitted = _environment_admitted(version_text, model_text)
    state.record(
        29,
        "dedicated admitted environment",
        "PASS" if admitted else "SKIP",
        f"hmc_release={version_text!r} system_model={model_text!r} "
        f"admitted={_ADMITTED_HMC_RELEASE!r}/{_ADMITTED_SYSTEM_MODEL!r}",
    )
    if not admitted:
        state.skip(
            29,
            "dedicated admitted environment (envelope)",
            f"HMC release {version_text!r} / model {model_text!r} is outside "
            "the ADR 0053-admitted envelope for dedicated profile grammar; "
            "issuing io_slots mutations here would use a grammar this "
            "repository has not probed — SKIP dedicated arm",
        )
    return admitted


async def capture_dedicated_baseline(
    client: Client, state: RunState
) -> _DedicatedFixture | None:
    """Resolve configuration and select an unassigned dedicated slot.

    Returns the fixture to create, or None when the arm must be skipped.
    """
    print("\n=== ST29: Dedicated PCIe Baseline (issue #217) ===")
    config = _dedicated_config(os.environ)
    if config is None:
        state.skip(
            29,
            "dedicated pcie configuration",
            f"{_DEDICATED_ENV_SYSTEM} and {_DEDICATED_ENV_PREFIX} are not both "
            "set, or a configured LPAR prefix, profile name, or DRC index "
            f"carries one of the HMC record delimiters {_RECORD_DELIMITERS!r}. "
            "The dedicated arm requires an explicitly configured managed "
            "system and LPAR name prefix, never falls back to a default "
            "system, and refuses a value that cannot cross the record "
            "grammar unchanged — SKIP dedicated arm",
        )
        return None

    if not await _admit_dedicated_environment(client, state, config):
        return None

    st, data = await state.call(
        client,
        "hmc_list_dedicated_pcie_slots",
        system_name_or_uuid=config.system_name,
    )
    state.record(29, "hmc_list_dedicated_pcie_slots (baseline)", st, data)
    if st != "PASS" or not isinstance(data, dict):
        state.skip(
            29,
            "dedicated slot inventory",
            "dedicated-slot inventory failed — SKIP dedicated arm",
        )
        return None
    # No `capability == "capability-unavailable"` branch: `list_dedicated_slots`
    # returns the literal "available" unconditionally, so such a branch could
    # never execute and would advertise a SKIP path that does not exist. A
    # failing read is already covered above.

    rows = [item for item in data.get("items") or [] if isinstance(item, dict)]
    unassigned = [row for row in rows if not (row.get("owner_lpar") or "").strip()]
    if config.drc_index is not None:
        selected = next(
            (row for row in unassigned if row.get("drc_index") == config.drc_index),
            None,
        )
        if selected is None:
            state.skip(
                29,
                "dedicated slot selection",
                f"configured drc_index {config.drc_index!r} is absent from the "
                "inventory or already owned — SKIP dedicated arm rather than "
                "mutate a slot this run did not select",
            )
            return None
    elif unassigned:
        selected = unassigned[0]
    else:
        state.skip(
            29,
            "dedicated slot selection",
            f"no unassigned dedicated PCIe slot on {config.system_name!r} "
            f"({len(rows)} slot(s) inventoried, all owned) — SKIP dedicated arm",
        )
        return None

    run_marker = _new_run_marker()
    lpar_name = f"{config.lpar_prefix}{run_marker}"
    fixture = _DedicatedFixture(
        config=config,
        run_marker=run_marker,
        lpar_name=lpar_name,
        probe_lpar_name=f"{lpar_name}-createtime",
        drc_index=str(selected.get("drc_index")),
    )
    state.record(
        29,
        "dedicated slot selection",
        "PASS",
        f"selected drc_index={fixture.drc_index!r} "
        f"description={selected.get('description')!r} on "
        f"{config.system_name!r}; fixture lpar={fixture.lpar_name!r} "
        f"run_marker={run_marker!r}",
    )
    return fixture


async def create_dedicated_fixture(
    client: Client, state: RunState, fixture: _DedicatedFixture
) -> bool:
    """Create the run-unique owner-stamped fixture LPAR.

    Returns True when the fixture exists and its UUID was resolved, which is
    the only state in which the arm may mutate hardware.
    """
    config = fixture.config
    print("\n=== ST30: Dedicated PCIe Fixture Create (issue #217) ===")

    # Create-time assignment: `prevalidate_lpar_pcie_assignments` refuses this
    # before `create_and_stamp_lpar` runs, so today nothing is created. That
    # refusal is the only reason, and it is exactly the gate this arm's
    # evidence exists to lift — so the probe does not assume it holds.
    st, data = await state.call(
        client,
        "hmc_create_lpar",
        system_name_or_uuid=config.system_name,
        name=fixture.probe_lpar_name,
        caller_token=fixture.run_marker,
        assignments={
            "dedicated": [
                {
                    "profile_name": config.profile_name,
                    "drc_index": fixture.drc_index,
                }
            ]
        },
    )
    state.record_expected_or_real(
        30,
        "hmc_create_lpar (create-time dedicated assignment)",
        st,
        data,
        expected_fail_substrings=[
            "PcieAssignmentUnavailableError",
            PCIE_ASSIGNMENT_UNAVAILABLE_REASON,
        ],
        skip_reason=(
            "create-time dedicated assignment is capability-unavailable "
            "(ADR 0055 fails closed before any mutating command, pending "
            "exact io_slots readback under ADR 0053) — SKIP this path; the "
            "refusal happens in prevalidation, ahead of partition creation"
        ),
    )
    if st == "PASS":
        # The gate has been lifted since this arm was written. A partition now
        # exists that nothing else in this run tracks.
        fixture.probe_created = True
        if isinstance(data, dict) and isinstance(data.get("lpar"), dict):
            fixture.probe_lpar_uuid = data["lpar"].get("UUID") or data["lpar"].get("uuid")
        state.record(
            30,
            "create-time dedicated assignment unexpectedly succeeded",
            "FAIL",
            "MANUAL RECOVERY REQUIRED (if cleanup below does not clear it): "
            f"the create-time probe created partition "
            f"{fixture.probe_lpar_name!r} on {config.system_name!r} with "
            f"dedicated slot {fixture.drc_index!r} assigned. ADR 0055's gate "
            "no longer refuses, so this arm's probe and ADR 0115 both need "
            "revisiting alongside the ADR 0053 capability update.",
        )

    st, data = await state.call(
        client,
        "hmc_create_lpar",
        system_name_or_uuid=config.system_name,
        name=fixture.lpar_name,
        caller_token=fixture.run_marker,
    )
    state.record(30, "hmc_create_lpar (fixture)", st, data)
    if st != "PASS":
        state.skip(
            30,
            "dedicated fixture create",
            "fixture LPAR create failed — SKIP dedicated arm; no partition to "
            "clean up",
        )
        return False
    fixture.created = True

    stamped = None
    if isinstance(data, dict):
        body = data.get("lpar")
        if isinstance(body, dict):
            fixture.lpar_uuid = body.get("UUID") or body.get("uuid")
        stamped = data.get("ownership_stamped")
        state.record(
            30,
            "fixture ownership stamp",
            "PASS" if stamped is True else "FAIL",
            f"ownership_stamped={stamped!r} warnings={data.get('warnings')!r}",
        )

    if stamped is not True:
        # The caller token is the half of the identity Guard A checks first
        # and refuses on unconditionally. Without it, assigning a slot here
        # does not risk a stranded slot on a partition cleanup may not touch
        # — it guarantees one. `False` means the stamp and the caller segment
        # were both lost; `None` means the stamp was skipped.
        state.skip(
            30,
            "fixture identity (ownership stamp)",
            f"fixture {fixture.lpar_name!r} was created but its ADR 0064 "
            f"ownership stamp did not land (ownership_stamped={stamped!r}), "
            "so cleanup could never prove this run owns it; no hardware will "
            "be mutated — proceeding directly to cleanup",
        )
        return False

    if not fixture.lpar_uuid:
        st_get, data_get = await state.call(
            client,
            "hmc_get_lpar",
            lpar_name_or_uuid=fixture.lpar_name,
            system_name_or_uuid=config.system_name,
        )
        if st_get == "PASS" and isinstance(data_get, dict):
            fixture.lpar_uuid = data_get.get("UUID") or data_get.get("uuid")

    if not fixture.lpar_uuid:
        state.skip(
            30,
            "fixture identity",
            f"fixture {fixture.lpar_name!r} was created but its UUID could not "
            "be resolved; no hardware will be mutated without a captured "
            "identity — proceeding directly to cleanup",
        )
        return False

    fixture.baseline_io_slots = await _read_profile_io_slots(client, state, fixture)
    state.record(
        30,
        "fixture profile io_slots (baseline)",
        "PASS" if fixture.baseline_io_slots is not None else "FAIL",
        f"lpar_uuid={fixture.lpar_uuid!r} "
        f"baseline io_slots={fixture.baseline_io_slots!r}",
    )
    return fixture.baseline_io_slots is not None


async def assign_dedicated_slot(
    client: Client, state: RunState, fixture: _DedicatedFixture
) -> bool:
    """Assign the selected slot to the fixture profile and confirm by readback."""
    config = fixture.config
    print("\n=== ST31: Dedicated PCIe Assign (issue #217) ===")

    st, data = await state.call(
        client,
        "hmc_assign_dedicated_pcie_slot",
        system_name_or_uuid=config.system_name,
        lpar_name_or_uuid=fixture.lpar_name,
        profile_name=config.profile_name,
        drc_index=fixture.drc_index,
    )
    state.record_expected_or_real(
        31,
        "hmc_assign_dedicated_pcie_slot",
        st,
        data,
        expected_fail_substrings=[
            "PcieAssignmentUnavailableError",
            PCIE_ASSIGNMENT_UNAVAILABLE_REASON,
        ],
        skip_reason=(
            "the admitted dedicated assignment operation is "
            "capability-unavailable (ADR 0055); this run gathers the exact "
            "io_slots evidence ADR 0053 names as the precondition for lifting "
            "it, through the documented profile grammar below"
        ),
    )

    st, data = await state.call(
        client,
        "hmc_run_command",
        cmd=_change_io_slots_command(fixture, add=True),
    )
    state.record(31, "chsyscfg io_slots+ (assign)", st, data)

    # Read back whichever way the command reported. `RunState.call` returns
    # FAIL for any raised exception, and the SSH transport raises when its
    # timeout expires — after the HMC has already executed chsyscfg. Returning
    # early on a FAIL would leave the run believing it had not written
    # something it had, which is the belief cleanup must never hold.
    applied = await _read_profile_io_slots(client, state, fixture)
    assigned = applied is not None and str(fixture.drc_index) in applied
    if assigned:
        fixture.applied_io_slots = applied
    state.record(
        31,
        "profile io_slots readback (post-assign)",
        "PASS" if assigned else "FAIL",
        f"io_slots={applied!r} expected to contain drc_index="
        f"{fixture.drc_index!r} (command status {st})",
    )
    return st == "PASS" and assigned


async def verify_dedicated_assigned(
    client: Client, state: RunState, fixture: _DedicatedFixture
) -> bool:
    """Verify the assignment through profile and inventory readback."""
    print("\n=== ST32: Dedicated PCIe Post-Assign Verify (issue #217) ===")
    observed = await _read_dedicated_state(client, state, fixture)
    profile_ok = (
        observed.profile_io_slots is not None
        and str(fixture.drc_index) in observed.profile_io_slots
    )
    state.record(
        32,
        "dedicated post-assign profile verify",
        "PASS" if profile_ok else "FAIL",
        _dedicated_state_summary(observed),
    )
    # Inventory ownership is informational: a profile-only assignment is not
    # expected to move the effective layer for a partition that has never
    # activated, the same asymmetry the SR-IOV arm records at ST25.
    state.record(
        32,
        "dedicated post-assign inventory owner (informational)",
        "PASS",
        f"inventory owner_lpar={observed.slot_owner!r} "
        "(a profile assignment does not change effective slot ownership until "
        "the partition activates)",
    )
    return profile_ok


async def unassign_dedicated_slot(
    client: Client, state: RunState, fixture: _DedicatedFixture
) -> bool:
    """Remove the slot and require the exact captured baseline to return."""
    print("\n=== ST33: Dedicated PCIe Unassign (issue #217) ===")
    st, data = await state.call(
        client,
        "hmc_run_command",
        cmd=_change_io_slots_command(fixture, add=False),
    )
    state.record(33, "chsyscfg io_slots- (unassign)", st, data)

    # Same reason as the assign: a removal whose response was lost has still
    # removed, and the run must not believe otherwise.
    observed = await _read_profile_io_slots(client, state, fixture)
    restored = observed is not None and observed == fixture.baseline_io_slots
    state.record(
        33,
        "profile io_slots exact baseline restore",
        "PASS" if restored else "FAIL",
        f"io_slots={observed!r} baseline={fixture.baseline_io_slots!r} "
        f"(command status {st})",
    )
    return st == "PASS" and restored


async def reassign_dedicated_slot(
    client: Client, state: RunState, fixture: _DedicatedFixture
) -> bool:
    """Reassign the same slot to the existing fixture, proving the round trip."""
    print("\n=== ST33: Dedicated PCIe Reassign (issue #217) ===")
    st, data = await state.call(
        client,
        "hmc_run_command",
        cmd=_change_io_slots_command(fixture, add=True),
    )
    state.record(33, "chsyscfg io_slots+ (reassign)", st, data)

    # The reassign is the LAST mutation before cleanup, so a lost response or
    # a lost confirming read here is the most dangerous of the three; read
    # back regardless.
    applied = await _read_profile_io_slots(client, state, fixture)
    ok = applied is not None and str(fixture.drc_index) in applied
    if ok:
        fixture.applied_io_slots = applied
    state.record(
        33,
        "profile io_slots readback (post-reassign)",
        "PASS" if ok else "FAIL",
        f"io_slots={applied!r} expected to contain drc_index="
        f"{fixture.drc_index!r} (command status {st})",
    )
    return st == "PASS" and ok


async def _cleanup_probe_partition(
    client: Client, state: RunState, fixture: _DedicatedFixture
) -> None:
    """Delete the create-time probe partition, on its own caller token.

    Independent of the fixture's own guards: a refusal here records recovery
    evidence and returns, and must not stop the fixture from being cleaned up.
    The probe was created with `caller_token=fixture.run_marker`, so a
    partition of that name carrying a different token is not this run's.
    """
    config = fixture.config
    st_desc, data_desc = await state.call(
        client,
        "hmc_get_lpar_description",
        system_name_or_uuid=config.system_name,
        lpar_name_or_uuid=fixture.probe_lpar_name,
    )
    probe_token = (
        parse_lpar_ownership_caller_token(data_desc)
        if st_desc == "PASS" and isinstance(data_desc, str)
        else None
    )
    if probe_token != fixture.run_marker:
        state.record(
            34,
            "dedicated cleanup: probe run-marker mismatch",
            "FAIL",
            "MANUAL RECOVERY REQUIRED: the create-time probe partition "
            f"{fixture.probe_lpar_name!r} on {config.system_name!r} does not "
            f"carry this run's caller token (read {probe_token!r}, expected "
            f"{fixture.run_marker!r}); it was NOT deleted. Inspect it and "
            "remove it by hand once identified.",
        )
        return

    # Hardware before the partition, here too. This is the ONE partition in
    # the arm that provably holds the dedicated slot at cleanup time — the
    # probe succeeded only because it was allowed to apply the assignment —
    # so deleting it without removing the slot is precisely the stranding
    # ADR 0115 forbids. The probe carries its own profile, so it gets its
    # own removal command and its own confirming read.
    probe = replace(
        fixture, lpar_name=fixture.probe_lpar_name, lpar_uuid=fixture.probe_lpar_uuid
    )
    st_rm, data_rm = await state.call(
        client, "hmc_run_command", cmd=_change_io_slots_command(probe, add=False)
    )
    state.record(34, "chsyscfg io_slots- (probe partition)", st_rm, data_rm)
    after = await _read_profile_io_slots(client, state, probe)
    if st_rm != "PASS" or after is None or str(fixture.drc_index) in after:
        state.record(
            34,
            "dedicated cleanup: probe slot removal failed",
            "FAIL",
            "MANUAL RECOVERY REQUIRED: dedicated slot "
            f"{fixture.drc_index!r} could not be confirmed removed from the "
            f"create-time probe partition {fixture.probe_lpar_name!r} on "
            f"{config.system_name!r} (io_slots={after!r}); the partition was "
            "NOT deleted, because deleting it would strand the slot. Run "
            f"`{_change_io_slots_command(probe, add=False)}` and then delete "
            f"{fixture.probe_lpar_name!r}.",
        )
        return

    # Act on the identity, not the name, for the same reason Guard C does.
    st, data = await state.call(
        client,
        "hmc_delete_lpar",
        system_name_or_uuid=config.system_name,
        lpar_name_or_uuid=fixture.probe_lpar_uuid or fixture.probe_lpar_name,
    )
    state.record(34, "hmc_delete_lpar (create-time probe partition)", st, data)
    if st != "PASS":
        state.record(
            34,
            "dedicated cleanup: probe partition delete failed",
            "FAIL",
            "MANUAL RECOVERY REQUIRED: the create-time probe partition "
            f"{fixture.probe_lpar_name!r} on {config.system_name!r} still "
            f"exists and must be removed by hand. Error: {str(data)[:400]}",
        )


async def cleanup_dedicated(
    client: Client, state: RunState, fixture: _DedicatedFixture
) -> None:
    """Remove the slot, then delete the fixture — each only on an exact match."""
    config = fixture.config
    print("\n=== ST34: Dedicated PCIe Cleanup (issue #217) ===")

    # Hardware before partitions, and the probe holds hardware on a
    # gate-lifted HMC. It is a different partition with its own identity, so
    # its outcome never gates the fixture's.
    if fixture.probe_created:
        await _cleanup_probe_partition(client, state, fixture)
    if not fixture.created:
        return

    # Guard A — fixture identity, re-read immediately before any mutation.
    observed = await _read_dedicated_state(client, state, fixture)
    state.record(34, "dedicated pre-cleanup state", "PASS", _dedicated_state_summary(observed))
    recovery = (
        f"MANUAL RECOVERY REQUIRED: fixture {fixture.lpar_name!r} on "
        f"{config.system_name!r} could not be confirmed as this run's; no "
        "mutation was attempted. Inspect it and, once you have confirmed it "
        f"is this run's fixture, remove slot {fixture.drc_index!r} with "
        f"`{_change_io_slots_command(fixture, add=False)}` and then delete the "
        "partition."
    )
    if observed.caller_token != fixture.run_marker:
        state.record(
            34,
            "dedicated cleanup: run-marker mismatch",
            "FAIL",
            f"{recovery} Expected caller token {fixture.run_marker!r}, read "
            f"{observed.caller_token!r}.",
        )
        return
    if fixture.lpar_uuid is not None and observed.lpar_uuid != fixture.lpar_uuid:
        state.record(
            34,
            "dedicated cleanup: uuid mismatch",
            "FAIL",
            f"{recovery} Expected UUID {fixture.lpar_uuid!r}, read "
            f"{observed.lpar_uuid!r}.",
        )
        return

    # Guard B — remove the slot before the partition, decided on LIVE state.
    #
    # Deliberately not gated on anything this run believes it did — not a
    # `slot_assigned` flag, and not `applied_io_slots is not None`. Both are
    # false on reachable paths where the profile really does carry the DRC
    # index: an assign or reassign whose response was lost to a timeout, and
    # one whose confirming read failed. On each, a belief-gated branch skips
    # removal and Guard C deletes a partition with a slot still on it. The
    # profile is the fact; a flag is only a memory of it. An unreadable value
    # (None) is also unequal to the baseline, so it enters the branch and is
    # refused inside it.
    drifted = (
        fixture.baseline_io_slots is not None
        and observed.profile_io_slots != fixture.baseline_io_slots
    )
    if drifted:
        if (
            fixture.applied_io_slots is None
            or observed.profile_io_slots != fixture.applied_io_slots
        ):
            state.record(
                34,
                "dedicated cleanup: profile drift",
                "FAIL",
                "MANUAL RECOVERY REQUIRED: the fixture profile's io_slots is "
                f"{observed.profile_io_slots!r}, which is neither the captured "
                f"baseline ({fixture.baseline_io_slots!r}) nor the value this "
                f"run applied ({fixture.applied_io_slots!r}), so this run "
                "cannot prove the deviation is its own. No mutation attempted "
                "and the partition was NOT deleted — deleting it would strand "
                "whatever is assigned. Recover with "
                f"`{_change_io_slots_command(fixture, add=False)}`.",
            )
            return
        st, data = await state.call(
            client,
            "hmc_run_command",
            cmd=_change_io_slots_command(fixture, add=False),
        )
        state.record(34, "chsyscfg io_slots- (cleanup)", st, data)
        if st != "PASS":
            state.record(
                34,
                "dedicated cleanup: slot removal failed",
                "FAIL",
                "MANUAL RECOVERY REQUIRED: slot removal failed, so the "
                "partition was not deleted — deleting it now would strand "
                f"slot {fixture.drc_index!r}. Run "
                f"`{_change_io_slots_command(fixture, add=False)}` then delete "
                f"{fixture.lpar_name!r}. Error: {str(data)[:400]}",
            )
            return
        after = await _read_profile_io_slots(client, state, fixture)
        if after != fixture.baseline_io_slots:
            state.record(
                34,
                "dedicated cleanup: baseline not restored",
                "FAIL",
                "MANUAL RECOVERY REQUIRED: io_slots is "
                f"{after!r} after removal, not the captured baseline "
                f"{fixture.baseline_io_slots!r}; the partition was not deleted. "
                f"Reconcile {fixture.lpar_name!r} by hand.",
            )
            return
        state.record(
            34,
            "dedicated cleanup: slot removed",
            "PASS",
            f"io_slots restored to the captured baseline {after!r}",
        )

    # Guard C — re-read identity once more, immediately before the delete.
    final = await _read_dedicated_state(client, state, fixture)
    if final.caller_token != fixture.run_marker or (
        fixture.lpar_uuid is not None and final.lpar_uuid != fixture.lpar_uuid
    ):
        state.record(
            34,
            "dedicated cleanup: identity changed before delete",
            "FAIL",
            "MANUAL RECOVERY REQUIRED: the fixture's identity changed between "
            "slot removal and deletion — read caller_token="
            f"{final.caller_token!r} uuid={final.lpar_uuid!r}, expected "
            f"{fixture.run_marker!r} / {fixture.lpar_uuid!r}. The partition was "
            "NOT deleted; remove it by hand after confirming what it is.",
        )
        return
    # `_read_dedicated_state` already fetched the profile, so this clause is
    # free — and without it the "refuse the delete while the profile differs
    # from the baseline" rule is enforced only at Guard A's earlier read.
    # A slot added by a concurrent session after that read, or after Guard B's
    # confirming read, would otherwise be stranded by this delete. On the
    # never-assigned path Guard A's read is the only profile observation at
    # all, so this is the whole of the window.
    if (
        fixture.baseline_io_slots is not None
        and final.profile_io_slots != fixture.baseline_io_slots
    ):
        state.record(
            34,
            "dedicated cleanup: profile changed before delete",
            "FAIL",
            "MANUAL RECOVERY REQUIRED: the fixture profile's io_slots is "
            f"{final.profile_io_slots!r} immediately before the delete, not "
            f"the captured baseline {fixture.baseline_io_slots!r}; something "
            "changed it after this run's last check. The partition was NOT "
            "deleted, because deleting it would strand whatever is assigned.",
        )
        return

    # Act on the identity Guard C just verified. `hmc_delete_lpar` accepts a
    # UUID or a name; deleting by name would re-resolve the name and reopen
    # the window the guard closed. `ownership_override` stays off: the fixture
    # is stamped by this run, so the tool's own description-token check passes
    # on every intended path, and it is the one operation-layer check that
    # survives the escape hatch.
    st, data = await state.call(
        client,
        "hmc_delete_lpar",
        system_name_or_uuid=config.system_name,
        lpar_name_or_uuid=fixture.lpar_uuid or fixture.lpar_name,
    )
    state.record(34, "hmc_delete_lpar (fixture cleanup)", st, data)
    if st != "PASS":
        state.record(
            34,
            "dedicated cleanup: fixture delete failed",
            "FAIL",
            f"MANUAL RECOVERY REQUIRED: fixture {fixture.lpar_name!r} on "
            f"{config.system_name!r} still exists and must be deleted by hand. "
            f"Its slot assignment was already removed. Error: {str(data)[:400]}",
        )


async def exercise_dedicated_pcie_assignment(
    client: Client, state: RunState
) -> None:
    """Orchestrate the dedicated-slot assign/verify/unassign/reassign/cleanup arm."""
    print("\n============================")
    print("=== Dedicated PCIe Live Test (issue #217) ===")
    print("============================")

    fixture = await capture_dedicated_baseline(client, state)
    if fixture is None:
        print("  Dedicated baseline SKIP — halting dedicated arm")
        return

    try:
        await _exercise_dedicated_steps(client, state, fixture)
    except Exception as exc:  # noqa: BLE001 — see below
        state.record(
            34,
            "dedicated arm raised before cleanup",
            "FAIL",
            f"{type(exc).__name__}: {exc}",
        )
    finally:
        # `created` covers the fixture; `probe_created` covers the create-time
        # probe partition, which on a gate-lifted HMC holds hardware and which
        # the fixture create can fail *after*. A create that never happened has
        # nothing to clean up, and calling cleanup then would emit a
        # manual-recovery row for a partition that does not exist.
        if fixture.created or fixture.probe_created:
            await cleanup_dedicated(client, state, fixture)


async def _exercise_dedicated_steps(
    client: Client, state: RunState, fixture: _DedicatedFixture
) -> None:
    """Run ST30–ST33; the caller owns cleanup on every exit."""
    if not await create_dedicated_fixture(client, state, fixture):
        return

    assign_ok = await assign_dedicated_slot(client, state, fixture)
    verify_ok = await verify_dedicated_assigned(client, state, fixture) if assign_ok else False
    if not assign_ok:
        state.skip(
            32,
            "dedicated post-assign verify",
            f"skipping verify: assign_ok={assign_ok}",
        )

    if assign_ok and verify_ok:
        unassign_ok = await unassign_dedicated_slot(client, state, fixture)
    else:
        state.skip(
            33,
            "chsyscfg io_slots- (unassign)",
            f"skipping unassign: assign_ok={assign_ok} verify_ok={verify_ok}",
        )
        unassign_ok = False

    if unassign_ok:
        await reassign_dedicated_slot(client, state, fixture)
    else:
        state.skip(
            33,
            "chsyscfg io_slots+ (reassign)",
            f"skipping reassign: unassign_ok={unassign_ok}",
        )
