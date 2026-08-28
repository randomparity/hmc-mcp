"""LPAR lifecycle and property scenarios for the live HMC test harness."""

from __future__ import annotations

import shlex
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from fastmcp import Client

from hmc_mcp.ssh.commands import build_filter
from hmc_mcp.ssh.lpar import validate_lpar_description


if TYPE_CHECKING:
    from live_test_runner import RunState


async def exercise_lpar_lifecycle(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST8: LPAR Lifecycle ===")

    if not context.system_uuid:
        st2, d2 = await state.call(
            client, "hmc_get_system", system_name_or_uuid=context.system_name
        )
        if st2 == "PASS" and isinstance(d2, dict):
            context.system_uuid = d2.get("UUID") or d2.get("uuid")

    st, data = await state.call(
        client,
        "hmc_create_lpar",
        system_name_or_uuid=context.system_name,
        name=context.scratch_name,
        desired_memory=512,
        max_memory=1024,
        desired_vcpus=1,
        max_vcpus=2,
    )
    state.record(8, "hmc_create_lpar", st, data)
    if st == "PASS" and isinstance(data, dict):
        context.scratch_uuid = data.get("uuid") or data.get("UUID")

    st, data = await state.call(
        client, "hmc_get_lpar", lpar_name_or_uuid=context.scratch_name
    )
    state.record(8, "hmc_get_lpar (confirm created)", st, data)
    if st == "PASS" and isinstance(data, dict) and not context.scratch_uuid:
        context.scratch_uuid = data.get("uuid") or data.get("UUID")

    st, data = await state.call(
        client,
        "hmc_modify_lpar",
        lpar_name_or_uuid=context.scratch_name,
        desired_memory=768,
        max_memory=1536,
    )
    state.record_expected_or_real(
        8,
        "hmc_modify_lpar",
        st,
        data,
        expected_fail_substrings=["406", "not acceptable"],
        skip_reason="HMC firmware returns HTTP 406 for REST LPAR modify (same limitation as create — REST write path unsupported)",
    )

    st, data = await state.call(
        client, "hmc_lpar_summary", lpar_name_or_uuid=context.scratch_name
    )
    state.record(8, "hmc_lpar_summary (post-modify)", st, data)

    st, data = await state.call(
        client, "hmc_power_on_lpar", lpar_name_or_uuid=context.scratch_name, wait=True
    )
    state.record(
        8,
        "hmc_power_on_lpar",
        st,
        data,
        "boot failure expected — no OS installed",
    )
    if st == "PASS" and isinstance(data, dict):
        context.job_uuid_sample = (
            data.get("job_uuid") or data.get("UUID") or context.job_uuid_sample
        )

    st, data = await state.call(
        client,
        "hmc_power_off_lpar",
        lpar_name_or_uuid=context.scratch_name,
        immediate=True,
        wait=True,
    )
    state.record(8, "hmc_power_off_lpar", st, data)
    if st == "PASS" and isinstance(data, dict) and not context.job_uuid_sample:
        context.job_uuid_sample = data.get("job_uuid") or data.get("UUID")

    st, data = await state.call(
        client, "hmc_delete_lpar", lpar_name_or_uuid=context.scratch_name
    )
    state.record(8, "hmc_delete_lpar", st, data)
    if st == "PASS":
        context.scratch_uuid = None

    st, data = await state.call(client, "hmc_list_lpars")
    state.record(8, "hmc_list_lpars (confirm deleted)", st, data)


# ---------------------------------------------------------------------------
# ST10 — LPAR Properties Mutations (SSH/CLI)
# ---------------------------------------------------------------------------


def _unrestorable_description(text: str) -> str | None:
    """Return why *text* cannot be written back, or ``None`` when it can.

    Defers to the server's own validator rather than restating its rules, so a
    baseline description the CLI cannot round-trip (non-ASCII, a control
    character, or a character the HMC's ``-i`` attribute record treats as
    structure — ADR 0045) is skipped rather than failing the restore.
    """
    if not text:
        return None
    try:
        validate_lpar_description(text)
    except ValueError as exc:
        return str(exc)
    return None


async def mutate_lpar_properties(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST10: LPAR Properties Mutations ===")

    orig_desc = context.lp3_baseline.get("description", "")
    if isinstance(orig_desc, dict):
        orig_desc = orig_desc.get("description") or ""

    # Description set/verify/restore — ASCII-only test string (fix #100)
    test_desc = "MCP live-test probe R2 safe to clear"
    st, data = await state.call(
        client,
        "hmc_set_lpar_description",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
        description=test_desc,
    )
    state.record(10, "hmc_set_lpar_description", st, data)

    st, data = await state.call(
        client,
        "hmc_get_lpar_description",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    state.record(10, "hmc_get_lpar_description (verify)", st, data)

    # Restore only if the original description can survive the CLI set command.
    _restore_desc = str(orig_desc) if orig_desc else ""
    _blocked = _unrestorable_description(_restore_desc)
    if _blocked:
        state.skip(
            10,
            "hmc_set_lpar_description (restore)",
            f"original description cannot be restored via CLI: {_blocked}",
        )
    else:
        st, data = await state.call(
            client,
            "hmc_set_lpar_description",
            system_name_or_uuid=context.system_name,
            lpar_name_or_uuid=context.lp3_name,
            description=_restore_desc,
        )
        state.record(10, "hmc_set_lpar_description (restore)", st, data)

    # Determine lp3 partition environment
    st_env, data_env = await state.call(
        client,
        "hmc_run_command",
        cmd=f"lssyscfg -r lpar -m {shlex.quote(context.system_name)}"
        f" --filter {shlex.quote(build_filter([('lpar_names', context.lp3_name)]))} -F lpar_env",
    )
    state.record(10, "lssyscfg lpar_env check", st_env, data_env)
    lp3_env = (data_env or "").strip() if st_env == "PASS" else ""

    if lp3_env == "vioserver":
        # Full toggle/verify/restore (only valid on VIOS partitions)
        orig_msp = context.lp3_baseline.get("msp")
        if isinstance(orig_msp, dict):
            orig_msp = orig_msp.get("msp") or orig_msp.get("enabled")
        new_msp = not bool(orig_msp)

        st, data = await state.call(
            client,
            "hmc_set_lpar_msp",
            system_name_or_uuid=context.system_name,
            lpar_name_or_uuid=context.lp3_name,
            enabled=new_msp,
        )
        state.record(10, "hmc_set_lpar_msp (toggle)", st, data)

        st, data = await state.call(
            client,
            "hmc_get_lpar_msp",
            system_name_or_uuid=context.system_name,
            lpar_name_or_uuid=context.lp3_name,
        )
        state.record(10, "hmc_get_lpar_msp (verify)", st, data)

        st, data = await state.call(
            client,
            "hmc_set_lpar_msp",
            system_name_or_uuid=context.system_name,
            lpar_name_or_uuid=context.lp3_name,
            enabled=bool(orig_msp),
        )
        state.record(10, "hmc_set_lpar_msp (restore)", st, data)
    else:
        # AIX/Linux partition — fix #102 should reject cleanly before SSH
        st_bad, data_bad = await state.call(
            client,
            "hmc_set_lpar_msp",
            system_name_or_uuid=context.system_name,
            lpar_name_or_uuid=context.lp3_name,
            enabled=True,
        )
        rejection_text = str(data_bad).lower()
        if st_bad == "FAIL" and (
            "only valid for a vios" in rejection_text
            or "vioserver" in rejection_text
            or "not found" in rejection_text
        ):
            state.record(
                10,
                "hmc_set_lpar_msp (non-VIOS rejection — expected)",
                "PASS",
                f"correctly rejected: {str(data_bad)[:200]}",
            )
        else:
            state.record(
                10,
                "hmc_set_lpar_msp (non-VIOS rejection)",
                st_bad,
                data_bad,
                f"lpar_env={lp3_env!r}",
            )
        state.skip(
            10,
            "hmc_set_lpar_msp (toggle/verify/restore)",
            f"lp3 is not a VIOS (lpar_env={lp3_env!r})",
        )

    # Proc compat — fetch actual current mode and set idempotently.
    # Use hmc_get_lpar_proc_compat to get the live value; fall back to
    # "default" only if the fetch fails. Skip if we can't get a real mode.
    st_pc, data_pc = await state.call(
        client,
        "hmc_get_lpar_proc_compat",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    if st_pc == "PASS" and isinstance(data_pc, dict):
        mode = (data_pc.get("desired") or data_pc.get("curr") or "").strip()
    else:
        mode = ""

    if mode and mode.lower() not in ("default", ""):
        st, data = await state.call(
            client,
            "hmc_set_lpar_proc_compat",
            system_name_or_uuid=context.system_name,
            lpar_name_or_uuid=context.lp3_name,
            mode=mode,
        )
        state.record(10, "hmc_set_lpar_proc_compat", st, data)
    else:
        state.skip(
            10,
            "hmc_set_lpar_proc_compat",
            f"proc compat mode is {mode!r} — skipping idempotent set (chsyscfg rejects 'default' as invalid attribute value)",
        )

    st, data = await state.call(
        client,
        "hmc_get_lpar_proc_compat",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    state.record(10, "hmc_get_lpar_proc_compat (verify)", st, data)

    # Profile sync
    st, data = await state.call(
        client,
        "hmc_sync_lpar_profile",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    state.record(10, "hmc_sync_lpar_profile", st, data)

    # Profile backup with force=True (fix #103)
    st, data = await state.call(
        client,
        "hmc_backup_lpar_profiles",
        system_name_or_uuid=context.system_name,
        file_path=str(Path(tempfile.gettempdir()) / "mcp-lp3-profiles-r2"),
        force=True,
    )
    state.record(10, "hmc_backup_lpar_profiles (force=True)", st, data)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# ST15 — Restore ltczz386-lp3 to Baseline
# ---------------------------------------------------------------------------


async def restore_lpar_baseline(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST15: Restore ltczz386-lp3 to Baseline ===")

    st, data = await state.call(
        client, "hmc_lpar_summary", lpar_name_or_uuid=context.lp3_name
    )
    state.record(15, "hmc_lpar_summary (post-test)", st, data)

    baseline = context.lp3_baseline

    # Restore description — only if the original survives the CLI (same guard as ST10)
    orig_desc = baseline.get("description", "")
    if isinstance(orig_desc, dict):
        orig_desc = orig_desc.get("description") or ""
    _restore_desc = str(orig_desc) if orig_desc else ""
    _blocked = _unrestorable_description(_restore_desc)
    if _blocked:
        state.skip(
            15,
            "hmc_set_lpar_description (restore)",
            f"original description cannot be restored via CLI: {_blocked}",
        )
    else:
        st, data = await state.call(
            client,
            "hmc_set_lpar_description",
            system_name_or_uuid=context.system_name,
            lpar_name_or_uuid=context.lp3_name,
            description=_restore_desc,
        )
        state.record(15, "hmc_set_lpar_description (restore)", st, data)

    # Restore proc compat — fetch live mode; skip if "default" (same guard as ST10)
    st_pc, data_pc = await state.call(
        client,
        "hmc_get_lpar_proc_compat",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    if st_pc == "PASS" and isinstance(data_pc, dict):
        mode = (data_pc.get("desired") or data_pc.get("curr") or "").strip()
    else:
        mode = ""

    if mode and mode.lower() not in ("default", ""):
        st, data = await state.call(
            client,
            "hmc_set_lpar_proc_compat",
            system_name_or_uuid=context.system_name,
            lpar_name_or_uuid=context.lp3_name,
            mode=mode,
        )
        state.record(15, "hmc_set_lpar_proc_compat (restore)", st, data)
    else:
        state.skip(
            15,
            "hmc_set_lpar_proc_compat (restore)",
            f"proc compat mode is {mode!r} — skipping restore (chsyscfg rejects 'default')",
        )

    # Final adapter audit
    st, data = await state.call(
        client,
        "hmc_list_adapters",
        lpar_name_or_uuid=context.lp3_name,
        adapter_type="ClientNetworkAdapter",
    )
    state.record(15, "hmc_list_adapters (final audit)", st, data)

    # Profile sync
    st, data = await state.call(
        client,
        "hmc_sync_lpar_profile",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    state.record(15, "hmc_sync_lpar_profile", st, data)

    # Final CLI dump
    st, data = await state.call(
        client,
        "hmc_run_command",
        cmd=f"lssyscfg -r lpar -m {shlex.quote(context.system_name)}"
        f" --filter {shlex.quote(build_filter([('lpar_names', context.lp3_name)]))}",
    )
    state.record(15, "hmc_run_command lssyscfg (final)", st, data)

    # Final summary
    st, data = await state.call(
        client, "hmc_lpar_summary", lpar_name_or_uuid=context.lp3_name
    )
    state.record(15, "hmc_lpar_summary (final confirm)", st, data)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# ST16 — VG Free-Space Check + Repository Create
# ---------------------------------------------------------------------------
