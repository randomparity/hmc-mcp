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
    print("\n=== ST8: LPAR Lifecycle ===")
    await _discover_system_uuid(client, state)
    await _create_and_confirm_scratch_lpar(client, state)
    await _modify_and_summarize_scratch_lpar(client, state)
    await _power_off_and_delete_scratch_lpar(client, state)


async def _discover_system_uuid(client: Client, state: RunState) -> None:
    """Discover the managed-system UUID when prior inventory did not provide it."""
    context = state.context
    if not context.system_uuid:
        status, data = await state.call(
            client, "hmc_get_system", system_name_or_uuid=context.system_name
        )
        if status == "PASS" and isinstance(data, dict):
            context.system_uuid = data.get("UUID") or data.get("uuid")


async def _create_and_confirm_scratch_lpar(client: Client, state: RunState) -> None:
    """Create the scratch LPAR and retain its identity from either response."""
    context = state.context
    status, data = await state.call(
        client,
        "hmc_create_lpar",
        system_name_or_uuid=context.system_name,
        name=context.scratch_name,
        resources={
            "desired_memory": context.scratch_create_desired_memory_mib,
            "max_memory": context.scratch_create_max_memory_mib,
            "desired_vcpus": context.scratch_create_desired_vcpus,
            "max_vcpus": context.scratch_create_max_vcpus,
        },
    )
    state.record(8, "hmc_create_lpar", status, data)
    if status == "PASS" and isinstance(data, dict):
        created = data.get("lpar")
        if isinstance(created, dict):
            context.scratch_uuid = created.get("uuid") or created.get("UUID")

    status, data = await state.call(
        client, "hmc_get_lpar", lpar_name_or_uuid=context.scratch_name
    )
    state.record(8, "hmc_get_lpar (confirm created)", status, data)
    if status == "PASS" and isinstance(data, dict) and not context.scratch_uuid:
        context.scratch_uuid = data.get("uuid") or data.get("UUID")


async def _modify_and_summarize_scratch_lpar(client: Client, state: RunState) -> None:
    """Exercise the known REST modification path and read its resulting summary."""
    context = state.context
    status, data = await state.call(
        client,
        "hmc_modify_lpar",
        lpar_name_or_uuid=context.scratch_name,
        resources={
            "desired_memory": context.scratch_modify_desired_memory_mib,
            "max_memory": context.scratch_modify_max_memory_mib,
        },
    )
    state.record_expected_or_real(
        8,
        "hmc_modify_lpar",
        status,
        data,
        expected_fail_substrings=["406", "not acceptable"],
        skip_reason="HMC firmware returns HTTP 406 for REST LPAR modify (same limitation as create — REST write path unsupported)",
    )

    status, data = await state.call(
        client, "hmc_lpar_summary", lpar_name_or_uuid=context.scratch_name
    )
    state.record(8, "hmc_lpar_summary (post-modify)", status, data)


async def _power_off_and_delete_scratch_lpar(client: Client, state: RunState) -> None:
    """Exercise power operations, then delete and confirm the scratch LPAR is gone."""
    context = state.context
    status, data = await state.call(
        client, "hmc_power_on_lpar", lpar_name_or_uuid=context.scratch_name, wait=True
    )
    state.record(
        8,
        "hmc_power_on_lpar",
        status,
        data,
        "boot failure expected — no OS installed",
    )
    if status == "PASS" and isinstance(data, dict):
        context.job_uuid_sample = (
            data.get("job_uuid") or data.get("UUID") or context.job_uuid_sample
        )

    status, data = await state.call(
        client,
        "hmc_power_off_lpar",
        lpar_name_or_uuid=context.scratch_name,
        immediate=True,
        wait=True,
    )
    state.record(8, "hmc_power_off_lpar", status, data)
    if status == "PASS" and isinstance(data, dict) and not context.job_uuid_sample:
        context.job_uuid_sample = data.get("job_uuid") or data.get("UUID")

    status, data = await state.call(
        client, "hmc_delete_lpar", lpar_name_or_uuid=context.scratch_name
    )
    state.record(8, "hmc_delete_lpar", status, data)
    if status == "PASS":
        context.scratch_uuid = None

    status, data = await state.call(client, "hmc_list_lpars")
    state.record(8, "hmc_list_lpars (confirm deleted)", status, data)


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


def _baseline_description(state: RunState) -> str:
    """Return the captured description in the writable string form."""
    description = state.context.lp3_baseline.get("description", "")
    if isinstance(description, dict):
        description = description.get("description") or ""
    return str(description) if description else ""


async def _restore_description(client: Client, state: RunState, scenario: int) -> None:
    """Restore the captured description when the CLI can represent it."""
    description = _baseline_description(state)
    blocked = _unrestorable_description(description)
    if blocked:
        state.skip(
            scenario,
            "hmc_set_lpar_description (restore)",
            f"original description cannot be restored via CLI: {blocked}",
        )
        return
    context = state.context
    status, data = await state.call(
        client,
        "hmc_set_lpar_description",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
        description=description,
    )
    state.record(scenario, "hmc_set_lpar_description (restore)", status, data)


async def _exercise_description_round_trip(client: Client, state: RunState) -> None:
    """Set, read, and restore an ASCII-safe LPAR description."""
    context = state.context
    status, data = await state.call(
        client,
        "hmc_set_lpar_description",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
        description="MCP live-test probe R2 safe to clear",
    )
    state.record(10, "hmc_set_lpar_description", status, data)
    status, data = await state.call(
        client,
        "hmc_get_lpar_description",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    state.record(10, "hmc_get_lpar_description (verify)", status, data)
    await _restore_description(client, state, 10)


async def _lpar_environment(client: Client, state: RunState) -> str:
    """Read the target partition environment through the HMC CLI."""
    context = state.context
    status, data = await state.call(
        client,
        "hmc_run_command",
        cmd=f"lssyscfg -r lpar -m {shlex.quote(context.system_name)}"
        f" --filter {shlex.quote(build_filter([('lpar_names', context.lp3_name)]))} -F lpar_env",
    )
    state.record(10, "lssyscfg lpar_env check", status, data)
    return (data or "").strip() if status == "PASS" else ""


async def _exercise_msp_behavior(client: Client, state: RunState) -> None:
    """Round-trip MSP on VIOS, or verify clean rejection on other LPARs."""
    context = state.context
    environment = await _lpar_environment(client, state)
    if environment == "vioserver":
        original = context.lp3_baseline.get("msp")
        if isinstance(original, dict):
            original = original.get("msp") or original.get("enabled")
        for label, enabled in (
            ("toggle", not bool(original)),
            ("restore", bool(original)),
        ):
            status, data = await state.call(
                client,
                "hmc_set_lpar_msp",
                system_name_or_uuid=context.system_name,
                lpar_name_or_uuid=context.lp3_name,
                enabled=enabled,
            )
            state.record(10, f"hmc_set_lpar_msp ({label})", status, data)
            if label == "toggle":
                status, data = await state.call(
                    client,
                    "hmc_get_lpar_msp",
                    system_name_or_uuid=context.system_name,
                    lpar_name_or_uuid=context.lp3_name,
                )
                state.record(10, "hmc_get_lpar_msp (verify)", status, data)
        return

    status, data = await state.call(
        client,
        "hmc_set_lpar_msp",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
        enabled=True,
    )
    rejection = str(data).lower()
    expected = status == "FAIL" and any(
        text in rejection
        for text in ("only valid for a vios", "vioserver", "not found")
    )
    if expected:
        state.record(
            10,
            "hmc_set_lpar_msp (non-VIOS rejection — expected)",
            "PASS",
            f"correctly rejected: {str(data)[:200]}",
        )
    else:
        state.record(
            10,
            "hmc_set_lpar_msp (non-VIOS rejection)",
            status,
            data,
            f"lpar_env={environment!r}",
        )
    state.skip(
        10,
        "hmc_set_lpar_msp (toggle/verify/restore)",
        f"lp3 is not a VIOS (lpar_env={environment!r})",
    )


async def _set_current_proc_compat(
    client: Client, state: RunState, scenario: int, action: str
) -> None:
    """Read and idempotently set the live non-default processor mode."""
    context = state.context
    status, data = await state.call(
        client,
        "hmc_get_lpar_proc_compat",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    mode = (
        (data.get("desired") or data.get("curr") or "").strip()
        if status == "PASS" and isinstance(data, dict)
        else ""
    )
    if mode and mode.lower() != "default":
        status, data = await state.call(
            client,
            "hmc_set_lpar_proc_compat",
            system_name_or_uuid=context.system_name,
            lpar_name_or_uuid=context.lp3_name,
            mode=mode,
        )
        state.record(scenario, action, status, data)
    else:
        state.skip(
            scenario,
            action,
            f"proc compat mode is {mode!r} — skipping idempotent set (chsyscfg rejects 'default')",
        )


async def _exercise_proc_compat(client: Client, state: RunState) -> None:
    """Set the current processor mode and verify the resulting value."""
    await _set_current_proc_compat(client, state, 10, "hmc_set_lpar_proc_compat")
    context = state.context
    status, data = await state.call(
        client,
        "hmc_get_lpar_proc_compat",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    state.record(10, "hmc_get_lpar_proc_compat (verify)", status, data)


async def _maintain_lpar_profile(client: Client, state: RunState) -> None:
    """Synchronize the active profile and exercise forced profile backup."""
    context = state.context
    status, data = await state.call(
        client,
        "hmc_sync_lpar_profile",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    state.record(10, "hmc_sync_lpar_profile", status, data)
    status, data = await state.call(
        client,
        "hmc_backup_lpar_profiles",
        system_name_or_uuid=context.system_name,
        file_path=str(Path(tempfile.gettempdir()) / "mcp-lp3-profiles-r2"),
        force=True,
    )
    state.record(10, "hmc_backup_lpar_profiles (force=True)", status, data)


async def mutate_lpar_properties(client: Client, state: RunState) -> None:
    """Run the ordered ST10 property checks against the baseline LPAR."""
    print("\n=== ST10: LPAR Properties Mutations ===")
    await _exercise_description_round_trip(client, state)
    await _exercise_msp_behavior(client, state)
    await _exercise_proc_compat(client, state)
    await _maintain_lpar_profile(client, state)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# ST15 — Restore the baseline LPAR
# ---------------------------------------------------------------------------


async def restore_lpar_baseline(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST15: Restore baseline LPAR ===")

    st, data = await state.call(
        client, "hmc_lpar_summary", lpar_name_or_uuid=context.lp3_name
    )
    state.record(15, "hmc_lpar_summary (post-test)", st, data)

    await _restore_description(client, state, 15)
    await _set_current_proc_compat(
        client, state, 15, "hmc_set_lpar_proc_compat (restore)"
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
