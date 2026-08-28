"""Destructive lifecycle scenarios for the live HMC test harness."""

from __future__ import annotations

import secrets
import shlex
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from fastmcp import Client

from hmc_mcp.ssh.commands import build_filter
from hmc_mcp.ssh.lpar import validate_lpar_description

from .results import entries, resource as get_resource

if TYPE_CHECKING:
    from live_test_runner import RunState

_TEST_USER_PASSWORD = f"Aa1!{secrets.token_hex(8)}"


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
# ST9 — Virtual Networking Mutations
# ---------------------------------------------------------------------------


async def mutate_virtual_networking(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST9: Virtual Networking Mutations ===")

    if context.test_vlan_id is None:
        for name in [
            "hmc_create_virtual_network",
            "hmc_create_lpar (nettest)",
            "hmc_add_network_adapter",
            "hmc_list_adapters (post-add)",
            "hmc_delete_adapter",
            "hmc_delete_virtual_network",
            "hmc_delete_lpar (nettest)",
        ]:
            state.skip(9, name, "no unused VLAN ID found in ST2")
        return

    vswitch_id = context.test_vswitch_id if context.test_vswitch_id is not None else 0

    st, data = await state.call(
        client,
        "hmc_create_virtual_network",
        system_name_or_uuid=context.system_name,
        name=f"mcp-test-vlan{context.test_vlan_id}",
        vlan_id=context.test_vlan_id,
        virtual_switch_id=vswitch_id,
        tagged=False,
    )
    state.record_expected_or_real(
        9,
        "hmc_create_virtual_network",
        st,
        data,
        expected_fail_substrings=["406", "not acceptable"],
        skip_reason="HMC firmware returns HTTP 406 for REST VirtualNetwork create (same PUT limitation as LPAR create)",
    )
    if st == "PASS" and isinstance(data, dict):
        context.test_network_uuid = data.get("uuid") or data.get("UUID")

    st, data = await state.call(
        client, "hmc_list_virtual_networks", system_name_or_uuid=context.system_name
    )
    state.record(9, "hmc_list_virtual_networks (post-create)", st, data)
    if st == "PASS" and not context.test_network_uuid:
        for e in entries(data):
            resource = get_resource(e)
            vlan = (
                resource.get("NetworkVLANID")
                or resource.get("VLANId")
                or resource.get("vlan_id")
            )
            if str(vlan) == str(context.test_vlan_id):
                context.test_network_uuid = e.get("UUID") or e.get("uuid")
                break

    # Use all_resources=1 path (no explicit resource args) — avoids HSCL0622
    # proc-unit validation failure on this HMC firmware.
    st, data = await state.call(
        client,
        "hmc_create_lpar",
        system_name_or_uuid=context.system_name,
        name=context.nettest_name,
    )
    state.record(9, "hmc_create_lpar (nettest)", st, data)
    if st == "PASS" and isinstance(data, dict):
        context.nettest_uuid = data.get("uuid") or data.get("UUID")

    if context.test_network_uuid:
        st, data = await state.call(
            client,
            "hmc_add_network_adapter",
            lpar_name_or_uuid=context.nettest_name,
            port_vlan_id=context.test_vlan_id,
            virtual_switch_id=vswitch_id,
        )
        state.record(9, "hmc_add_network_adapter", st, data)

        st, data = await state.call(
            client,
            "hmc_list_adapters",
            lpar_name_or_uuid=context.nettest_name,
            adapter_type="ClientNetworkAdapter",
        )
        state.record(9, "hmc_list_adapters (post-add)", st, data)
        if st == "PASS":
            for e in entries(data):
                context.test_adapter_uuid = e.get("UUID") or e.get("uuid")
                break
    else:
        state.skip(
            9,
            "hmc_add_network_adapter",
            "virtual network not created (REST 406)",
        )
        state.skip(
            9,
            "hmc_list_adapters (post-add)",
            "virtual network not created (REST 406)",
        )

    if context.test_adapter_uuid:
        st, data = await state.call(
            client,
            "hmc_delete_adapter",
            lpar_name_or_uuid=context.nettest_name,
            adapter_type="ClientNetworkAdapter",
            adapter_uuid=context.test_adapter_uuid,
        )
        state.record(9, "hmc_delete_adapter", st, data)
    else:
        state.skip(9, "hmc_delete_adapter", "no adapter UUID captured")

    if context.test_network_uuid:
        st, data = await state.call(
            client,
            "hmc_delete_virtual_network",
            system_name_or_uuid=context.system_name,
            network_uuid=context.test_network_uuid,
        )
        state.record(9, "hmc_delete_virtual_network", st, data)
        if st == "PASS":
            context.test_network_uuid = None
    else:
        state.skip(9, "hmc_delete_virtual_network", "no network UUID captured")

    if context.nettest_uuid:
        st, data = await state.call(
            client, "hmc_delete_lpar", lpar_name_or_uuid=context.nettest_name
        )
        state.record(9, "hmc_delete_lpar (nettest)", st, data)
        if st == "PASS":
            context.nettest_uuid = None
    else:
        state.skip(9, "hmc_delete_lpar (nettest)", "nettest LPAR not created")


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
# ST11 — User Administration
# ---------------------------------------------------------------------------

_REST000E_SKIP = ["REST000E", "400", "not available on this HMC"]


async def administer_test_user(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST11: User Administration ===")

    st, data = await state.call(
        client,
        "hmc_create_user",
        name=context.test_user,
        taskrole="viewer",
        password=_TEST_USER_PASSWORD,
        description="MCP live test user R2",
    )
    state.record_expected_or_real(
        11,
        "hmc_create_user",
        st,
        data,
        expected_fail_substrings=_REST000E_SKIP,
        skip_reason="HmcUser REST not supported on this HMC (expected)",
    )
    user_created = st == "PASS"

    st, data = await state.call(client, "hmc_list_users")
    state.record_expected_or_real(
        11,
        "hmc_list_users (confirm created)",
        st,
        data,
        expected_fail_substrings=_REST000E_SKIP,
        skip_reason="HmcUser REST not supported (expected)",
    )

    if user_created:
        st, data = await state.call(
            client,
            "hmc_modify_user",
            name=context.test_user,
            description="MCP live test user R2 — updated",
        )
        state.record(11, "hmc_modify_user", st, data)
    else:
        state.skip(11, "hmc_modify_user", "user not created (REST000E expected)")

    if user_created:
        st, data = await state.call(client, "hmc_delete_user", name=context.test_user)
        state.record(11, "hmc_delete_user", st, data)
    else:
        state.skip(11, "hmc_delete_user", "user not created (REST000E expected)")

    st, data = await state.call(client, "hmc_list_users")
    state.record_expected_or_real(
        11,
        "hmc_list_users (confirm deleted)",
        st,
        data,
        expected_fail_substrings=_REST000E_SKIP,
        skip_reason="HmcUser REST not supported (expected)",
    )


# ---------------------------------------------------------------------------
# ST12 — PCM Metrics & Job Monitoring
# ---------------------------------------------------------------------------


async def inspect_metrics_jobs(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST12: PCM Metrics & Job Monitoring ===")

    st, data = await state.call(
        client,
        "hmc_get_pcm_preferences",
        category="ManagedSystem",
        resource_name_or_uuid=context.system_name,
    )
    state.record_expected_or_real(
        12,
        "hmc_get_pcm_preferences",
        st,
        data,
        expected_fail_substrings=["PCM", "406", "403"],
        skip_reason="PCM not licensed on this HMC (expected)",
    )
    current_ltm = None
    if st == "PASS" and isinstance(data, dict):
        if "long_term_monitor" in data:
            current_ltm = data["long_term_monitor"]
        else:
            current_ltm = data.get("LongTermMonitorEnabled")

    if current_ltm is not None:
        new_ltm = not bool(current_ltm)
        st, data = await state.call(
            client,
            "hmc_set_pcm_preferences",
            category="ManagedSystem",
            resource_name_or_uuid=context.system_name,
            long_term_monitor=new_ltm,
        )
        state.record(12, "hmc_set_pcm_preferences (toggle)", st, data)

        st, data = await state.call(
            client,
            "hmc_get_pcm_preferences",
            category="ManagedSystem",
            resource_name_or_uuid=context.system_name,
        )
        state.record(12, "hmc_get_pcm_preferences (verify)", st, data)

        st, data = await state.call(
            client,
            "hmc_set_pcm_preferences",
            category="ManagedSystem",
            resource_name_or_uuid=context.system_name,
            long_term_monitor=bool(current_ltm),
        )
        state.record(12, "hmc_set_pcm_preferences (restore)", st, data)
    else:
        state.skip(
            12,
            "hmc_set_pcm_preferences",
            "PCM not licensed/enabled on this HMC (expected)",
        )

    job_uuid = context.job_uuid_sample
    if job_uuid:
        st, data = await state.call(client, "hmc_get_job", job_uuid=job_uuid)
        state.record_expected_or_real(
            12,
            "hmc_get_job",
            st,
            data,
            expected_fail_substrings=["REST000E", "REST000B", "400"],
            skip_reason="Job REST type not supported on this HMC firmware",
        )

        st, data = await state.call(
            client,
            "hmc_wait_for_job",
            job_uuid=job_uuid,
            timeout_seconds=10,
            poll_interval=2,
        )
        state.record_expected_or_real(
            12,
            "hmc_wait_for_job",
            st,
            data,
            expected_fail_substrings=["REST000E", "REST000B", "400"],
            skip_reason="Job REST type not supported on this HMC firmware",
        )
    else:
        state.skip(12, "hmc_get_job", "no job UUID captured (ST8 may have failed)")
        state.skip(12, "hmc_wait_for_job", "no job UUID")

    st, data = await state.call(client, "hmc_list_recent_jobs", limit=20)
    state.record(12, "hmc_list_recent_jobs (post-tests)", st, data)
    # Opportunistically capture a job UUID if we still don't have one
    if not context.job_uuid_sample and st == "PASS":
        for e in entries(data):
            if isinstance(e, dict) and e.get("type") != "error":
                context.job_uuid_sample = e.get("UUID") or e.get("uuid")
                break


# ---------------------------------------------------------------------------
# ST13 — Provision Dry Run
# ---------------------------------------------------------------------------


async def validate_provisioning_dry_run(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST13: Provision Dry Run ===")

    # Prefer lp3's own PVID (always present on the system); fall back to test VLAN
    pvid = context.lp3_baseline.get("pvid") or context.test_vlan_id
    vios_uuid = context.vios_uuid
    vios_pid = context.vios_partition_id or context.lp3_baseline.get(
        "vios_partition_id"
    )
    vios_slot = context.lp3_baseline.get("vios_slot") or 2

    if not vios_uuid or not pvid:
        reason = "no VIOS UUID" if not vios_uuid else "no PVID or test VLAN ID"
        state.skip(13, "hmc_provision_lpar (dry_run)", reason)
        return

    st, data = await state.call(
        client,
        "hmc_provision_lpar",
        dry_run=True,
        system_name_or_uuid=context.system_name,
        name="ltczz386-lp3-dry",
        port_vlan_id=int(pvid),
        vios_uuid=vios_uuid,
        vios_partition_id=int(vios_pid or 2),
        vios_slot=int(vios_slot),
        storage_name="test-dry-disk",
        desired_memory=512,
    )
    state.record(13, "hmc_provision_lpar (dry_run)", st, data)
    if st == "PASS" and isinstance(data, dict):
        steps = data.get("steps") or []
        all_dry = all(s.get("status") == "dry_run" for s in steps)
        print(f"  dry_run steps: {[s.get('step') for s in steps]}")
        print(f"  all status=dry_run: {all_dry}")


# ---------------------------------------------------------------------------
# ST14 — Storage Lifecycle + Full Live Provision of ltczz386-lp3
# ---------------------------------------------------------------------------


async def _remove_previous_test_lpar(client: Client, state: RunState) -> None:
    """Power off and delete the prior test partition, then verify its absence."""
    context = state.context
    status, _ = await state.call(
        client, "hmc_get_lpar", lpar_name_or_uuid=context.lp3_name
    )
    if status == "PASS":
        status, data = await state.call(
            client,
            "hmc_power_off_lpar",
            lpar_name_or_uuid=context.lp3_name,
            immediate=True,
            wait=True,
        )
        state.record(14, "hmc_power_off_lpar", status, data)
        status, data = await state.call(
            client, "hmc_delete_lpar", lpar_name_or_uuid=context.lp3_name
        )
        state.record(14, "hmc_delete_lpar", status, data)
    else:
        reason = "lp3 not found — already deleted in previous run"
        state.skip(14, "hmc_power_off_lpar", reason)
        state.skip(14, "hmc_delete_lpar", reason)

    status, data = await state.call(client, "hmc_list_lpars")
    state.record(14, "hmc_list_lpars (confirm lp3 gone)", status, data)


async def _recreate_test_disk(
    client: Client,
    state: RunState,
    vios_uuid: str,
    vg_uuid: str,
    vdisk_size_mb: int,
) -> None:
    """Remove any stale VIOS logical volume and create a fresh virtual disk."""
    context = state.context
    status, data = await state.call(
        client, "hmc_list_volume_groups", vios_name_or_uuid=vios_uuid
    )
    state.record(14, "hmc_list_volume_groups (pre-create)", status, data)

    vg_name = context.vdisk_vg_name or "VG1"
    command = (
        f"viosvrcmd -m {context.system_name} -p {context.vios_uuid}"
        f' -c "rmvlog -vg {vg_name} -lv {context.vdisk_name}"'
    )
    status, data = await state.call(client, "hmc_run_command", cmd=command)
    state.record_expected_or_real(
        14,
        "hmc_run_command rmvlog (delete old VG1-lp3)",
        status,
        data,
        expected_fail_substrings=[
            "does not exist",
            "not found",
            "No such",
            "0516-306",
            "0516-404",
        ],
        skip_reason="VG1-lp3 LV not present on VIOS (already cleaned up or never existed)",
    )

    status, data = await state.call(
        client,
        "hmc_create_virtual_disk",
        vios_name_or_uuid=vios_uuid,
        vg_uuid=vg_uuid,
        disk_name=context.vdisk_name,
        capacity_mib=vdisk_size_mb,
    )
    state.record_expected_or_real(
        14,
        "hmc_create_virtual_disk (VG1-lp3)",
        status,
        data,
        expected_fail_substrings=["406", "not acceptable"],
        skip_reason="REST VolumeGroup POST not supported on this HMC firmware — "
        "pre-existing VG1-lp3 LV must be recreated manually on the VIOS",
    )

    status, data = await state.call(
        client, "hmc_list_volume_groups", vios_name_or_uuid=vios_uuid
    )
    state.record(14, "hmc_list_volume_groups (post-create)", status, data)


async def _provision_from_baseline(
    client: Client,
    state: RunState,
    *,
    vios_uuid: str,
    vg_uuid: str,
    pvid: int,
    vios_slot: int,
    vios_pid: int,
) -> None:
    """Build and submit the live provision request from captured baseline resources."""
    context = state.context
    baseline_lpar = context.lp3_baseline.get("lpars") or {}
    resource = get_resource(baseline_lpar) if isinstance(baseline_lpar, dict) else {}
    status, data = await state.call(
        client,
        "hmc_provision_lpar",
        system_name_or_uuid=context.system_name,
        name=context.lp3_name,
        port_vlan_id=pvid,
        vios_uuid=vios_uuid,
        vios_partition_id=vios_pid,
        vios_slot=vios_slot,
        storage_name=context.vdisk_name,
        storage_kind="VirtualDisk",
        vg_uuid=vg_uuid,
        min_memory=int(
            resource.get("MinimumMemory") or resource.get("minimum_memory") or 256
        ),
        desired_memory=int(
            resource.get("DesiredMemory") or resource.get("desired_memory") or 1024
        ),
        max_memory=int(
            resource.get("MaximumMemory") or resource.get("maximum_memory") or 2048
        ),
        desired_vcpus=int(
            resource.get("DesiredVirtualProcessors")
            or resource.get("desired_virtual_processors")
            or 1
        ),
        max_vcpus=int(
            resource.get("MaximumVirtualProcessors")
            or resource.get("maximum_virtual_processors")
            or 2
        ),
        partition_type="AIX/Linux",
        power_on=True,
        dry_run=False,
    )
    state.record(14, "hmc_provision_lpar (live)", status, data)
    if status == "PASS" and isinstance(data, dict):
        for step in data.get("steps") or []:
            step_status = step.get("status", "unknown")
            icon = "✅" if step_status == "ok" else "❌"
            print(f"    {icon} provision step [{step.get('step', '?')}]: {step_status}")


async def exercise_storage_provisioning(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST14: Storage Lifecycle + Full Live Provision of ltczz386-lp3 ===")

    baseline = context.lp3_baseline
    vios_uuid = context.vios_uuid
    vg_uuid = context.vg_uuid
    vdisk_size_mb = context.vdisk_size_mb
    pvid = baseline.get("pvid")
    vios_slot = baseline.get("vios_slot")
    vios_pid = context.vios_partition_id or baseline.get("vios_partition_id")

    missing = [
        k
        for k, v in {
            "vios_uuid": vios_uuid,
            "vg_uuid": vg_uuid,
            "pvid": pvid,
            "vios_slot": vios_slot,
            "vios_pid": vios_pid,
            "vdisk_size_mb": vdisk_size_mb,
        }.items()
        if not v
    ]
    if missing:
        state.record(
            14,
            "pre-flight check",
            "FAIL",
            f"Missing required context keys: {missing}. "
            f"Re-run ST0 and ST3 before ST14.",
        )
        for name in [
            "hmc_power_off_lpar",
            "hmc_delete_lpar",
            "hmc_list_volume_groups (pre-create)",
            "hmc_create_virtual_disk",
            "hmc_list_volume_groups (post-create)",
            "hmc_provision_lpar (live)",
            "hmc_lpar_summary (post-provision)",
        ]:
            state.skip(14, name, "pre-flight failed")
        return

    state.record(
        14,
        "pre-flight check",
        "PASS",
        f"vios_uuid={vios_uuid} vg_uuid={vg_uuid} pvid={pvid} "
        f"vios_slot={vios_slot} vios_pid={vios_pid} vdisk_mb={vdisk_size_mb}",
    )

    await _remove_previous_test_lpar(client, state)
    await _recreate_test_disk(
        client,
        state,
        str(vios_uuid),
        str(vg_uuid),
        int(vdisk_size_mb),
    )
    await _provision_from_baseline(
        client,
        state,
        vios_uuid=str(vios_uuid),
        vg_uuid=str(vg_uuid),
        pvid=int(pvid),
        vios_slot=int(vios_slot),
        vios_pid=int(vios_pid),
    )

    # Confirm lp3 is back
    st, data = await state.call(
        client, "hmc_get_lpar", lpar_name_or_uuid=context.lp3_name
    )
    state.record(14, "hmc_get_lpar (post-provision)", st, data)
    if st == "PASS" and isinstance(data, dict):
        context.lp3_uuid = data.get("uuid") or data.get("UUID")

    st, data = await state.call(
        client, "hmc_lpar_summary", lpar_name_or_uuid=context.lp3_name
    )
    state.record(14, "hmc_lpar_summary (post-provision)", st, data)


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
