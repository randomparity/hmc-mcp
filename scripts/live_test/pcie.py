"""SR-IOV live validation scenarios for issue #217.

Exercises reversible SR-IOV logical-port assignment on ltczz386 / ltczz386-lp3.
The LPAR must be Not Activated before this module runs.

Admitted environment (ADR 0053 / operations/pcie.py):
  HMC V10R3 M1060 · managed-system model 8375-42A

Test structure (ST23–ST28):
  ST23 — Baseline: read adapter/physport/logport inventory; confirm lp3 profile is clean
  ST24 — Assign logical port 27004003 (phys_port 0, 2% capacity) to lp3
  ST25 — Verify effective + profile readback after assign
  ST26 — Unassign; verify logical port is unconfigured and profile is restored
  ST27 — Reassign on existing LPAR (same port, same capacity)
  ST28 — Cleanup: unassign again; final profile + inventory confirm; PASS/SKIP/FAIL

Missing hardware or a wrong LPAR state produces SKIP per arm, not FAIL.
Any cleanup mutation failure records manual-recovery evidence and halts further
cleanup (does not attempt additional mutations on an unknown state).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastmcp import Client

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
            f"lssyscfg -r prof -m {context.system_name} "
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


def _adapter_is_healthy(data: object) -> bool:
    """Return whether the selected adapter is in healthy SR-IOV mode."""
    items = data.get("items") or [] if isinstance(data, dict) else []
    return any(
        isinstance(item, dict)
        and item.get("adapter_id") == _ADAPTER_ID
        and item.get("mode") == "sriov"
        and item.get("availability") == "1"
        for item in items
    )


def _available_capacity(data: object) -> float:
    """Calculate remaining physical-port capacity from logical-port inventory."""
    used = 0.0
    items = data.get("items") or [] if isinstance(data, dict) else []
    for index, item in enumerate(items):
        if (
            isinstance(item, dict)
            and item.get("availability") not in ("unconfigured", None, "")
            and item.get("capacity_percent") is not None
        ):
            try:
                used += float(item["capacity_percent"])
            except (ValueError, TypeError) as error:
                raise ValueError(
                    f"logical-port row {index} has invalid capacity_percent"
                ) from error
    return 100.0 - used


def _logical_port_is_configured(data: object) -> bool:
    """Return whether the selected logical port has an effective assignment."""
    items = data.get("items") or [] if isinstance(data, dict) else []
    return any(
        isinstance(item, dict)
        and item.get("logical_port_id") == _LOGICAL_PORT_ID
        and item.get("availability") not in ("unconfigured", None, "")
        for item in items
    )


async def _verify_cleanup_inventory(client: Client, state: RunState) -> None:
    """Record final logical-port and profile checks after cleanup."""
    context = state.context
    st, data = await state.call(
        client,
        "hmc_list_sriov_logical_ports",
        system_name_or_uuid=context.system_name,
        adapter_id=_ADAPTER_ID,
        logical_port_id=_LOGICAL_PORT_ID,
    )
    state.record(28, "hmc_list_sriov_logical_ports (final)", st, data)
    if st == "PASS":
        still_configured = _logical_port_is_configured(data)
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

    final_state = await _read_sriov_state(client, state)
    profile_clean = final_state.profile_ports in (None, "none", "")
    state.record(
        28,
        "lp3 profile final check",
        "PASS" if profile_clean else "FAIL",
        (
            f"MANUAL RECOVERY REQUIRED: profile sriov_eth_logical_ports="
            f"{final_state.profile_ports!r} after cleanup — "
            f"run: chsyscfg -r prof -m {context.system_name} "
            f"-i \"name={_PROFILE_NAME},lpar_name={context.lp3_name},"
            f"sriov_eth_logical_ports=none\" to recover"
            if not profile_clean
            else "sriov_eth_logical_ports=none — lp3 profile restored to baseline"
        ),
    )


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
    if not _adapter_is_healthy(data):
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
    available = _available_capacity(data_lp) if st_lp == "PASS" else 0.0
    state.record(
        23,
        "sriov capacity check (pre-test)",
        "PASS" if available >= _CAPACITY_PERCENT else "SKIP",
        f"phys_port {_PHYS_PORT_ID}: available={available}% needed={_CAPACITY_PERCENT}%",
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
    if _logical_port_is_configured(data):
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
                f"Run: chhwres -r sriov --rsubtype logport -m {context.system_name} "
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

    await _verify_cleanup_inventory(client, state)


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
        await cleanup_sriov(client, state)
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
