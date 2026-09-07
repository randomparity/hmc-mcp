"""Provisioning scenarios for the live HMC test harness."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp import Client

from .observation import ExpectedOutcome
from .results import resource as get_resource

if TYPE_CHECKING:
    from live_test_runner import RunState

_TEST_DISK_ABSENT = ExpectedOutcome(
    reason="test disk is not present on VIOS (already cleaned up or never existed)",
    error_codes=frozenset(
        {"does not exist", "not found", "No such", "0516-306", "0516-404"}
    ),
)
_VOLUME_GROUP_POST_UNSUPPORTED = ExpectedOutcome(
    reason="REST VolumeGroup POST not supported on this HMC firmware — "
    "pre-existing test disk must be recreated manually on the VIOS",
    error_codes=frozenset({"406", "not acceptable"}),
)

# ---------------------------------------------------------------------------
# ST13 — Provision Dry Run
# ---------------------------------------------------------------------------


async def validate_provisioning_dry_run(client: Client, state: RunState) -> None:
    config = state.config
    artifacts = state.artifacts
    print("\n=== ST13: Provision Dry Run ===")

    # Prefer lp3's own PVID (always present on the system); fall back to test VLAN
    pvid = artifacts.lp3_baseline.get("pvid") or artifacts.test_vlan_id
    vios_uuid = artifacts.vios_uuid
    vios_pid = artifacts.vios_partition_id or artifacts.lp3_baseline.get(
        "vios_partition_id"
    )
    vios_slot = artifacts.lp3_baseline.get("vios_slot") or config.dry_run_vios_slot

    if not vios_uuid or not pvid:
        reason = "no VIOS UUID" if not vios_uuid else "no PVID or test VLAN ID"
        state.skip(13, "hmc_provision_lpar (dry_run)", reason)
        return

    st, data = await state.call(
        client,
        "hmc_provision_lpar",
        dry_run=True,
        system_name_or_uuid=config.system_name,
        name=config.dry_run_lpar_name,
        adapters={
            "port_vlan_id": int(pvid),
            "vios_partition_id": int(vios_pid or config.dry_run_vios_partition_id),
            "vios_slot": int(vios_slot),
        },
        storage={
            "vios_uuid": vios_uuid,
            "storage_name": config.dry_run_storage_name,
        },
        resources={"desired_memory": config.dry_run_memory_mib},
    )
    state.record(13, "hmc_provision_lpar (dry_run)", st, data)
    if st == "PASS" and isinstance(data, dict):
        steps = data.get("steps") or []
        all_dry = all(s.get("status") == "dry_run" for s in steps)
        print(f"  dry_run steps: {[s.get('step') for s in steps]}")
        print(f"  all status=dry_run: {all_dry}")


# ---------------------------------------------------------------------------
# ST14 — Storage Lifecycle + Full Live Provision
# ---------------------------------------------------------------------------


async def _remove_previous_test_lpar(client: Client, state: RunState) -> None:
    """Power off and delete the prior test partition, then verify its absence."""
    config = state.config
    status, _ = await state.call(
        client, "hmc_get_lpar", lpar_name_or_uuid=config.lp3_name
    )
    if status == "PASS":
        status, data = await state.call(
            client,
            "hmc_power_off_lpar",
            lpar_name_or_uuid=config.lp3_name,
            immediate=True,
            wait=True,
        )
        state.record(14, "hmc_power_off_lpar", status, data)
        status, data = await state.call(
            client,
            "hmc_delete_lpar",
            system_name_or_uuid=config.system_name,
            lpar_name_or_uuid=config.lp3_name,
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
    config = state.config
    artifacts = state.artifacts
    status, data = await state.call(
        client, "hmc_list_volume_groups", vios_name_or_uuid=vios_uuid
    )
    state.record(14, "hmc_list_volume_groups (pre-create)", status, data)

    vg_name = artifacts.vdisk_vg_name or config.vdisk_volume_group_name
    command = (
        f"viosvrcmd -m {config.system_name} -p {artifacts.vios_uuid}"
        f' -c "rmvlog -vg {vg_name} -lv {config.vdisk_name}"'
    )
    status, data = await state.call(client, "hmc_run_command", cmd=command)
    state.record_with_expected(
        14,
        "hmc_run_command rmvlog (delete old test disk)",
        status,
        data,
        [_TEST_DISK_ABSENT],
    )

    status, data = await state.call(
        client,
        "hmc_create_virtual_disk",
        vios_name_or_uuid=vios_uuid,
        vg_uuid=vg_uuid,
        disk_name=config.vdisk_name,
        capacity_mib=vdisk_size_mib,
    )
    state.record_with_expected(
        14,
        "hmc_create_virtual_disk (test disk)",
        status,
        data,
        [_VOLUME_GROUP_POST_UNSUPPORTED],
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
    config = state.config
    status, data = await state.call(
        client,
        "hmc_provision_lpar",
        system_name_or_uuid=config.system_name,
        name=config.lp3_name,
        adapters={
            "port_vlan_id": pvid,
            "vios_partition_id": vios_pid,
            "vios_slot": vios_slot,
        },
        storage={
            "vios_uuid": vios_uuid,
            "storage_name": config.vdisk_name,
            "kind": "VirtualDisk",
            "vg_uuid": vg_uuid,
        },
        resources=_baseline_provision_resources(state),
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


def _baseline_provision_resources(state: RunState) -> dict[str, int]:
    """Translate the captured REST resource keys into provision request fields."""
    config = state.config
    artifacts = state.artifacts
    baseline_lpar = artifacts.lp3_baseline.get("lpars") or {}
    resource = get_resource(baseline_lpar) if isinstance(baseline_lpar, dict) else {}
    values = (
        (
            "min_memory",
            "MinimumMemory",
            "minimum_memory",
            config.provision_min_memory_mib,
        ),
        (
            "desired_memory",
            "DesiredMemory",
            "desired_memory",
            config.provision_desired_memory_mib,
        ),
        (
            "max_memory",
            "MaximumMemory",
            "maximum_memory",
            config.provision_max_memory_mib,
        ),
        (
            "desired_vcpus",
            "DesiredVirtualProcessors",
            "desired_virtual_processors",
            config.provision_desired_vcpus,
        ),
        (
            "max_vcpus",
            "MaximumVirtualProcessors",
            "maximum_virtual_processors",
            config.provision_max_vcpus,
        ),
    )
    return {
        name: int(resource.get(upper) or resource.get(lower) or default)
        for name, upper, lower, default in values
    }


async def exercise_storage_provisioning(client: Client, state: RunState) -> None:
    config = state.config
    artifacts = state.artifacts
    print("\n=== ST14: Storage Lifecycle + Full Live Provision ===")

    baseline = artifacts.lp3_baseline
    vios_uuid = artifacts.vios_uuid
    vg_uuid = artifacts.vg_uuid
    vdisk_size_mib = artifacts.vdisk_size_mib
    pvid = baseline.get("pvid")
    vios_slot = baseline.get("vios_slot")
    vios_pid = artifacts.vios_partition_id or baseline.get("vios_partition_id")

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
        client, "hmc_get_lpar", lpar_name_or_uuid=config.lp3_name
    )
    state.record(14, "hmc_get_lpar (post-provision)", st, data)
    if st == "PASS" and isinstance(data, dict):
        artifacts.lp3_uuid = data.get("uuid") or data.get("UUID")

    st, data = await state.call(
        client, "hmc_lpar_summary", lpar_name_or_uuid=config.lp3_name
    )
    state.record(14, "hmc_lpar_summary (post-provision)", st, data)
