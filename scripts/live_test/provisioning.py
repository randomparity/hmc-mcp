"""Provisioning scenarios for the live HMC test harness."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp import Client


from .results import resource as get_resource

if TYPE_CHECKING:
    from live_test_runner import RunState

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
    vdisk_size_mib: int,
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
        capacity_mib=vdisk_size_mib,
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
    vdisk_size_mib = context.vdisk_size_mib
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
            "vdisk_size_mib": vdisk_size_mib,
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
        f"vios_slot={vios_slot} vios_pid={vios_pid} vdisk_mib={vdisk_size_mib}",
    )

    await _remove_previous_test_lpar(client, state)
    await _recreate_test_disk(
        client,
        state,
        str(vios_uuid),
        str(vg_uuid),
        int(vdisk_size_mib),
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
