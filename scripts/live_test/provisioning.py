"""Provisioning scenarios for the live HMC test harness."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp import Client

from .network import listed_vlans
from .observation import judge_create_result, plain_data
from .results import resource as get_resource
from .storage import configured_vg_uuid
from .storage_lifecycle import LISTING_NAME, volume_listing, volume_names

if TYPE_CHECKING:
    from live_test_runner import RunState

# ---------------------------------------------------------------------------
# ST13 — Provision Dry Run
# ---------------------------------------------------------------------------


async def validate_provisioning_dry_run(client: Client, state: RunState) -> None:
    config = state.config
    artifacts = state.artifacts
    print("\n=== ST13: Provision Dry Run ===")

    vios_uuid = artifacts.vios_uuid

    if not vios_uuid:
        state.skip(13, "hmc_provision_lpar (dry_run)", "no VIOS UUID")
        return

    st, data = await state.call(
        client,
        "hmc_provision_lpar",
        dry_run=True,
        system_name_or_uuid=config.system_name,
        name=config.dry_run_lpar_name,
        adapters={"port_vlan_id": config.provision_vlan_id},
        storage={
            "vios_uuid": vios_uuid,
            "storage_name": config.dry_run_storage_name,
        },
        resources={"desired_memory": config.dry_run_memory_mib},
    )
    state.record(13, "hmc_provision_lpar (dry_run)", st, data)
    data = plain_data(data)
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


async def _test_disk_names(client: Client, state: RunState) -> frozenset[str] | None:
    config = state.config
    vios_id = state.artifacts.vios_partition_id
    if type(vios_id) is not int or vios_id <= 0:
        state.record(
            14, "test disk inventory", "FAIL", "positive VIOS partition ID required"
        )
        return None
    status, data = await state.call(
        client,
        "hmc_run_command",
        cmd=volume_listing(config.system_name, vios_id, config.vdisk_volume_group_name),
    )
    lines = (
        [line.split() for line in data.splitlines() if line.strip()]
        if isinstance(data, str)
        else []
    )
    valid = (
        len(lines) >= 2
        and lines[0] == [f"{config.vdisk_volume_group_name}:"]
        and lines[1]
        == ["LV", "NAME", "TYPE", "LPs", "PPs", "PVs", "LV", "STATE", "MOUNT", "POINT"]
        and all(
            len(row) == 7
            and LISTING_NAME.fullmatch(row[0])
            and all(value.isdecimal() for value in row[2:5])
            for row in lines[2:]
        )
    )
    names = volume_names(data) if status == "PASS" and valid else None
    state.record(
        14, "test disk inventory", "PASS" if names is not None else "FAIL", data
    )
    return names


async def _recreate_test_disk(
    client: Client,
    state: RunState,
    vios_uuid: str,
    vg_uuid: str,
    vdisk_size_mib: int,
) -> bool:
    """Remove any stale VIOS logical volume and create a fresh virtual disk."""
    config = state.config
    status, data = await state.call(
        client, "hmc_list_volume_groups", vios_name_or_uuid=vios_uuid
    )
    state.record(14, "hmc_list_volume_groups (pre-create)", status, data)

    names = await _test_disk_names(client, state)
    if names is None:
        return False
    if config.vdisk_name not in names:
        state.skip(
            14,
            "hmc_delete_virtual_disk (old test disk)",
            "test disk absent in VIOS inventory",
        )
    else:
        status, data = await state.call(
            client,
            "hmc_delete_virtual_disk",
            vios_name_or_uuid=vios_uuid,
            system_name_or_uuid=config.system_name,
            vg_uuid=vg_uuid,
            disk_name=config.vdisk_name,
        )
        state.record(14, "hmc_delete_virtual_disk (old test disk)", status, data)
        if status != "PASS":
            return False
        names = await _test_disk_names(client, state)
        if names is None:
            return False
        if config.vdisk_name in names:
            state.record(
                14,
                "old test disk removal readback",
                "FAIL",
                "test disk remains on VIOS",
            )
            return False

    # No refusal is declared expected: ST40 re-checks the create live, so a
    # refused create is a failure, not a known gap (#1348).
    status, data = await state.call(
        client,
        "hmc_create_virtual_disk",
        vios_name_or_uuid=vios_uuid,
        vg_uuid=vg_uuid,
        disk_name=config.vdisk_name,
        capacity_mib=vdisk_size_mib,
    )
    state.record(14, "hmc_create_virtual_disk (test disk)", status, data)

    status, data = await state.call(
        client, "hmc_list_volume_groups", vios_name_or_uuid=vios_uuid
    )
    state.record(14, "hmc_list_volume_groups (post-create)", status, data)
    return True


async def _provision_from_baseline(
    client: Client,
    state: RunState,
    *,
    vios_uuid: str,
    vg_uuid: str,
    pvid: int,
) -> None:
    """Build and submit the live provision request from captured baseline resources."""
    config = state.config
    status, data = await state.call(
        client,
        "hmc_provision_lpar",
        system_name_or_uuid=config.system_name,
        name=config.lp3_name,
        adapters={"port_vlan_id": pvid},
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
    record_status, reason = judge_create_result(status, data)
    state.record(14, "hmc_provision_lpar (live)", record_status, data, reason)
    data = plain_data(data)
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


async def _provision_vlan_refusal(client: Client, state: RunState) -> str | None:
    """Say why the configured VLAN cannot be provisioned on, or None when it can.

    The VLAN is operator-configured rather than the test partition's own, and
    `hmc_provision_lpar` checks it only after ST14 has deleted the partition and
    disk, so ST14 checks it first (#970).
    """
    vlan_id = state.config.provision_vlan_id
    status, data = await state.call(
        client,
        "hmc_list_virtual_networks",
        system_name_or_uuid=state.config.system_name,
    )
    state.record(14, "hmc_list_virtual_networks (provision VLAN)", status, data)
    fix = "set LIVE_TEST_PROVISION_VLAN_ID to a VLAN with a virtual network"
    if status != "PASS":
        return f"hmc_list_virtual_networks returned {status}; cannot confirm VLAN {vlan_id}"
    vlans, malformed = listed_vlans(data)
    if vlan_id in vlans:
        return None
    note = (
        f" (unparsable VLAN identifiers: {', '.join(repr(v) for v in malformed)})"
        if malformed
        else ""
    )
    return f"no virtual network on VLAN {vlan_id}{note}; {fix}"


async def exercise_storage_provisioning(client: Client, state: RunState) -> None:
    config = state.config
    artifacts = state.artifacts
    print("\n=== ST14: Storage Lifecycle + Full Live Provision ===")

    vios_uuid = artifacts.vios_uuid
    vg_uuid = configured_vg_uuid(state)

    missing = [
        k for k, v in {"vios_uuid": vios_uuid, "vg_uuid": vg_uuid}.items() if not v
    ]
    if type(artifacts.vios_partition_id) is not int or artifacts.vios_partition_id <= 0:
        missing.append("vios_partition_id")
    refusal = (
        f"Missing required context keys: {missing}. Re-run ST0 and ST3 before ST14."
        if missing
        else await _provision_vlan_refusal(client, state)
    )
    if refusal:
        state.record(14, "pre-flight check", "FAIL", refusal)
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
        f"vios_uuid={vios_uuid} vg_uuid={vg_uuid} vlan={config.provision_vlan_id} "
        f"vdisk_mib={config.provision_disk_mib}",
    )

    await _remove_previous_test_lpar(client, state)
    if not await _recreate_test_disk(
        client,
        state,
        str(vios_uuid),
        str(vg_uuid),
        config.provision_disk_mib,
    ):
        return
    await _provision_from_baseline(
        client,
        state,
        vios_uuid=str(vios_uuid),
        vg_uuid=str(vg_uuid),
        pvid=config.provision_vlan_id,
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
