"""Read-only inventory scenarios for the live HMC test harness."""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

from fastmcp import Client

from hmc_mcp.ssh_commands import build_filter

from .results import entries, resource as get_resource

if TYPE_CHECKING:
    from live_test_runner import RunState


async def capture_lpar_baseline(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST0: Capture ltczz386-lp3 Baseline ===")

    # 1. Basic LPAR info
    st, data = await state.call(
        client, "hmc_get_lpar", lpar_name_or_uuid=context.lp3_name
    )
    state.record(0, "hmc_get_lpar (baseline)", st, data)
    if st == "PASS" and isinstance(data, dict):
        context.lp3_uuid = data.get("uuid") or data.get("UUID")
        context.lp3_baseline["lpars"] = data

    # 2. Composite summary
    st, data = await state.call(
        client, "hmc_lpar_summary", lpar_name_or_uuid=context.lp3_name
    )
    state.record(0, "hmc_lpar_summary (baseline)", st, data)
    if st == "PASS":
        context.lp3_baseline["summary"] = data

    # 3. Description
    st, data = await state.call(
        client,
        "hmc_get_lpar_description",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    state.record(0, "hmc_get_lpar_description (baseline)", st, data)
    if st == "PASS":
        # Store the plain string value, not the raw result dict, so ST10/ST15
        # restore guards don't have to unwrap a nested dict at restore time.
        desc_val = data
        if isinstance(desc_val, dict):
            desc_val = desc_val.get("description") or desc_val.get("value") or ""
        context.lp3_baseline["description"] = str(desc_val) if desc_val else ""

    # 4. MSP flag
    st, data = await state.call(
        client,
        "hmc_get_lpar_msp",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    state.record(0, "hmc_get_lpar_msp (baseline)", st, data)
    if st == "PASS":
        context.lp3_baseline["msp"] = data

    # 5. Proc compat
    st, data = await state.call(
        client,
        "hmc_get_lpar_proc_compat",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    state.record(0, "hmc_get_lpar_proc_compat (baseline)", st, data)
    if st == "PASS":
        context.lp3_baseline["proc_compat"] = data

    # 6. CNA adapters — capture PVID and vswitch ID for ST14
    st, data = await state.call(
        client,
        "hmc_list_adapters",
        lpar_name_or_uuid=context.lp3_name,
        adapter_type="ClientNetworkAdapter",
    )
    state.record(0, "hmc_list_adapters CNA (baseline)", st, data)
    if st == "PASS":
        context.lp3_baseline["cna_adapters"] = data
        for e in entries(data):
            resource = get_resource(e)
            pvid = resource.get("PortVLANID") or resource.get("port_vlan_id")
            if pvid:
                context.lp3_baseline["pvid"] = int(pvid)
                context.lp3_baseline["vswitch_id"] = int(
                    resource.get("VirtualSwitchID")
                    or resource.get("virtual_switch_id")
                    or 0
                )
                break

    # 7. vSCSI adapters — capture VIOS partition ID and VIOS server slot for ST14
    st, data = await state.call(
        client,
        "hmc_list_adapters",
        lpar_name_or_uuid=context.lp3_name,
        adapter_type="VirtualSCSIClientAdapter",
    )
    state.record(0, "hmc_list_adapters vSCSI (baseline)", st, data)
    if st == "PASS":
        context.lp3_baseline["vscsi_adapters"] = data
        for e in entries(data):
            resource = get_resource(e)
            # The HMC REST API uses RemoteLogicalPartitionID/RemoteSlotNumber
            # on the client adapter to describe the VIOS side.  The nested
            # ServerAdapter block holds the VIOS's VirtualSlotNumber.
            vios_pid = (
                resource.get("RemoteLogicalPartitionID")
                or resource.get("remote_logical_partition_id")
                or resource.get("ServerPartitionID")
                or resource.get("server_partition_id")
            )
            server_adapter = resource.get("ServerAdapter") or {}
            vios_slot = (
                server_adapter.get("VirtualSlotNumber")
                or server_adapter.get("virtual_slot_number")
                or resource.get("RemoteSlotNumber")
                or resource.get("remote_slot_number")
                or resource.get("ServerAdapterID")
                or resource.get("server_adapter_id")
            )
            if vios_pid is not None:
                context.lp3_baseline["vios_partition_id"] = int(vios_pid)
            if vios_slot is not None:
                context.lp3_baseline["vios_slot"] = int(vios_slot)
            break

    # 8. VIOS — capture UUID and numeric PartitionID scoped to our managed system
    st, data = await state.call(
        client, "hmc_list_vios", system_name_or_uuid=context.system_name
    )
    state.record(0, "hmc_list_vios (baseline)", st, data)
    if st == "PASS":
        for e in entries(data):
            resource = get_resource(e)
            uuid = e.get("UUID") or e.get("uuid")
            pid = resource.get("PartitionID") or resource.get("partition_id")
            if uuid:
                context.vios_uuid = uuid
                context.vios_partition_id = int(pid) if pid is not None else None
                break

    # 9. Full CLI dump
    st, data = await state.call(
        client,
        "hmc_run_command",
        cmd=f"lssyscfg -r lpar -m {shlex.quote(context.system_name)}"
        f" --filter {shlex.quote(build_filter([('lpar_names', context.lp3_name)]))}",
    )
    state.record(0, "hmc_run_command lssyscfg (baseline)", st, data)
    if st == "PASS":
        context.lp3_baseline["lssyscfg"] = data

    print(f"  lp3 UUID: {context.lp3_uuid}")
    print(f"  VIOS UUID: {context.vios_uuid}  PartitionID: {context.vios_partition_id}")
    print(
        f"  lp3 PVID: {context.lp3_baseline.get('pvid')}  "
        f"vSCSI VIOS slot: {context.lp3_baseline.get('vios_slot')}"
    )
    print(f"  Baseline keys: {list(context.lp3_baseline.keys())}")


# ---------------------------------------------------------------------------
# ST1 — Connectivity & Inventory
# ---------------------------------------------------------------------------


async def inventory_connectivity(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST1: Connectivity & Inventory ===")

    st, data = await state.call(client, "hmc_console_info")
    state.record(1, "hmc_console_info", st, data)
    if st == "PASS" and isinstance(data, dict):
        context.console_uuid = data.get("uuid") or data.get("UUID")

    st, data = await state.call(client, "hmc_list_systems")
    state.record(1, "hmc_list_systems (list)", st, data)
    if st == "PASS":
        for e in entries(data):
            resource = get_resource(e)
            if (
                context.system_name.lower()
                in (resource.get("SystemName") or "").lower()
            ):
                context.system_uuid = e.get("UUID")
                break
        if not context.system_uuid:
            first = entries(data)
            if first:
                context.system_uuid = first[0].get("UUID")

    st, data = await state.call(
        client, "hmc_get_system", system_name_or_uuid=context.system_name
    )
    state.record(1, "hmc_get_system (single)", st, data)
    # Fall back: extract system UUID from the single-system lookup if the list
    # returned empty (e.g. HMC firmware bug on unfiltered ManagedSystem feed)
    if st == "PASS" and isinstance(data, dict) and not context.system_uuid:
        context.system_uuid = data.get("UUID") or data.get("uuid")
    print(f"  System UUID: {context.system_uuid}")

    st, data = await state.call(client, "hmc_list_lpars")
    state.record(1, "hmc_list_lpars (list)", st, data)

    st, data = await state.call(
        client, "hmc_get_lpar", lpar_name_or_uuid=context.lp3_name
    )
    state.record(1, "hmc_get_lpar (single lp3)", st, data)
    if st == "PASS" and isinstance(data, dict) and not context.lp3_uuid:
        context.lp3_uuid = data.get("uuid") or data.get("UUID")

    st, data = await state.call(
        client, "hmc_list_vios", system_name_or_uuid=context.system_name
    )
    state.record(1, "hmc_list_vios", st, data)
    if st == "PASS" and not context.vios_uuid:
        for e in entries(data):
            resource = get_resource(e)
            uuid = e.get("UUID") or e.get("uuid")
            pid = resource.get("PartitionID") or resource.get("partition_id")
            if uuid:
                context.vios_uuid = uuid
                context.vios_partition_id = int(pid) if pid is not None else None
                break
    print(f"  VIOS UUID: {context.vios_uuid}  PartitionID: {context.vios_partition_id}")

    st, data = await state.call(client, "hmc_capacity_report")
    state.record(1, "hmc_capacity_report", st, data)

    st, data = await state.call(client, "hmc_find_placement", desired_memory_mb=1024)
    state.record(1, "hmc_find_placement", st, data)

    st, data = await state.call(
        client, "hmc_get_system", system_name_or_uuid=context.system_name
    )
    state.record(1, "hmc_get_system", st, data)

    st, data = await state.call(
        client, "hmc_list_resources", resource_type="LogicalPartition"
    )
    state.record(1, "hmc_list_resources", st, data)

    st, data = await state.call(client, "hmc_list_recent_jobs", limit=10)
    state.record(1, "hmc_list_recent_jobs", st, data)
    if st == "PASS":
        for e in entries(data):
            if isinstance(e, dict) and e.get("type") != "error":
                context.job_uuid_sample = e.get("UUID") or e.get("uuid")
                break

    st, data = await state.call(
        client, "hmc_system_summary", system_name_or_uuid=context.system_name
    )
    state.record(1, "hmc_system_summary", st, data)

    st, data = await state.call(
        client, "hmc_lpar_summary", lpar_name_or_uuid=context.lp3_name
    )
    state.record(1, "hmc_lpar_summary", st, data)


# ---------------------------------------------------------------------------
# ST2 — Network Inventory
# ---------------------------------------------------------------------------


async def inventory_network(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST2: Network Inventory ===")

    st, data = await state.call(
        client, "hmc_list_virtual_switches", system_name_or_uuid=context.system_name
    )
    state.record(2, "hmc_list_virtual_switches", st, data)
    if st == "PASS":
        for e in entries(data):
            resource = get_resource(e)
            sid = resource.get("SwitchID") or resource.get("switch_id")
            if sid is not None:
                context.test_vswitch_id = int(sid)
                break
        if context.test_vswitch_id is None:
            context.test_vswitch_id = 0

    st, data = await state.call(
        client, "hmc_list_virtual_networks", system_name_or_uuid=context.system_name
    )
    state.record(2, "hmc_list_virtual_networks", st, data)
    if st == "PASS":
        used_vlans: set[int] = set()
        for e in entries(data):
            resource = get_resource(e)
            vlan = (
                resource.get("NetworkVLANID")
                or resource.get("VLANId")
                or resource.get("vlan_id")
            )
            if vlan is not None:
                try:
                    used_vlans.add(int(vlan))
                except (TypeError, ValueError):
                    pass
        for candidate in range(3000, 3100):
            if candidate not in used_vlans:
                context.test_vlan_id = candidate
                break
    print(
        f"  Test VLAN ID: {context.test_vlan_id}  VSwitch ID: {context.test_vswitch_id}"
    )

    st, data = await state.call(
        client, "hmc_list_network_bridges", system_name_or_uuid=context.system_name
    )
    state.record(2, "hmc_list_network_bridges", st, data)

    st, data = await state.call(
        client, "hmc_list_fc_ports", system_name_or_uuid=context.system_name
    )
    state.record(2, "hmc_list_fc_ports", st, data)

    st, data = await state.call(
        client, "hmc_list_sea_adapters", system_name_or_uuid=context.system_name
    )
    state.record(2, "hmc_list_sea_adapters", st, data)

    st, data = await state.call(
        client,
        "hmc_list_adapters",
        lpar_name_or_uuid=context.lp3_name,
        adapter_type="ClientNetworkAdapter",
    )
    state.record(2, "hmc_list_adapters (CNA lp3)", st, data)


# ---------------------------------------------------------------------------
# ST3 — Storage & SSP Inventory
# ---------------------------------------------------------------------------


async def inventory_storage(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST3: Storage & SSP Inventory ===")

    if context.vios_uuid:
        st, data = await state.call(
            client, "hmc_list_volume_groups", vios_name_or_uuid=context.vios_uuid
        )
        state.record(3, "hmc_list_volume_groups", st, data)
        if st == "PASS":
            for vg in entries(data):
                resource = get_resource(vg)
                vg_name = resource.get("GroupName") or resource.get("group_name") or ""
                uuid = vg.get("UUID") or vg.get("uuid")
                # Accept any VG that contains our target disk; fall back to first
                vdisks = (
                    resource.get("VirtualDisks") or resource.get("virtual_disks") or []
                )
                if isinstance(vdisks, dict):
                    # May be wrapped: {"VirtualDisk": [...]} or {"VirtualDisk": {...}}
                    vdisks = vdisks.get("VirtualDisk") or []
                if isinstance(vdisks, dict):
                    vdisks = [vdisks]
                found_target_disk = False
                for vd in vdisks if isinstance(vdisks, list) else []:
                    virtual_disk_resource = get_resource(vd)
                    if virtual_disk_resource.get("DiskName") == context.vdisk_name:
                        found_target_disk = True
                        raw = virtual_disk_resource.get(
                            "DiskCapacity"
                        ) or virtual_disk_resource.get("disk_capacity")
                        if raw is not None:
                            try:
                                # DiskCapacity is in GB — convert to MB
                                gb = int(float(raw))
                                context.vdisk_size_mb = gb * 1024
                            except (TypeError, ValueError):
                                pass
                if found_target_disk or not context.vg_uuid:
                    context.vg_uuid = uuid
                    context.vdisk_vg_name = vg_name
                if found_target_disk:
                    break  # found the VG containing lp3's disk
        print(f"  VG UUID: {context.vg_uuid}  vdisk_size_mb: {context.vdisk_size_mb}")
    else:
        state.skip(
            3,
            "hmc_list_volume_groups",
            "no VIOS UUID in context (ST0/ST1 failed)",
        )

    st, data = await state.call(client, "hmc_list_clusters")
    state.record(3, "hmc_list_clusters", st, data)

    st, data = await state.call(client, "hmc_list_shared_storage_pools")
    state.record(3, "hmc_list_shared_storage_pools", st, data)

    st, data = await state.call(
        client, "hmc_list_io_slots", system_name_or_uuid=context.system_name
    )
    state.record(3, "hmc_list_io_slots", st, data)

    st, data = await state.call(
        client, "hmc_list_memory_pools", system_name_or_uuid=context.system_name
    )
    state.record(3, "hmc_list_memory_pools", st, data)


# ---------------------------------------------------------------------------
# ST4 — LPAR Properties & Profile Inventory
# ---------------------------------------------------------------------------


async def inventory_lpar_profiles(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST4: LPAR Properties & Profile Inventory ===")

    st, data = await state.call(
        client,
        "hmc_get_lpar_description",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    state.record(4, "hmc_get_lpar_description", st, data)

    st, data = await state.call(
        client,
        "hmc_get_lpar_msp",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    state.record(4, "hmc_get_lpar_msp", st, data)

    st, data = await state.call(
        client, "hmc_get_proc_compat_modes", system_name_or_uuid=context.system_name
    )
    state.record(4, "hmc_get_proc_compat_modes", st, data)

    st, data = await state.call(
        client,
        "hmc_get_lpar_proc_compat",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    state.record(4, "hmc_get_lpar_proc_compat", st, data)

    st, data = await state.call(
        client,
        "hmc_list_vnics",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    state.record(4, "hmc_list_vnics", st, data)

    st, data = await state.call(
        client,
        "hmc_get_lpar_memopt_score",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    state.record(4, "hmc_get_lpar_memopt_score", st, data)

    st, data = await state.call(
        client, "hmc_list_lpar_memopt_scores", system_name_or_uuid=context.system_name
    )
    state.record(4, "hmc_list_lpar_memopt_scores", st, data)

    st, data = await state.call(
        client, "hmc_get_system_memopt_score", system_name_or_uuid=context.system_name
    )
    state.record(4, "hmc_get_system_memopt_score", st, data)

    st, data = await state.call(
        client, "hmc_plan_lpar_memopt_scores", system_name_or_uuid=context.system_name
    )
    state.record(4, "hmc_plan_lpar_memopt_scores", st, data)

    st, data = await state.call(
        client, "hmc_plan_system_memopt_score", system_name_or_uuid=context.system_name
    )
    state.record(4, "hmc_plan_system_memopt_score", st, data)

    st, data = await state.call(
        client,
        "hmc_list_resource_group_memopt_scores",
        system_name_or_uuid=context.system_name,
    )
    state.record(4, "hmc_list_resource_group_memopt_scores", st, data)

    st, data = await state.call(
        client,
        "hmc_plan_resource_group_memopt_scores",
        system_name_or_uuid=context.system_name,
    )
    state.record(4, "hmc_plan_resource_group_memopt_scores", st, data)

    st, data = await state.call(
        client,
        "hmc_get_minimum_affinity_policy",
        system_name_or_uuid=context.system_name,
        lpar_name_or_uuid=context.lp3_name,
    )
    state.record(4, "hmc_get_minimum_affinity_policy", st, data)


# ---------------------------------------------------------------------------
# ST5 — Metrics & Templates
# ---------------------------------------------------------------------------


async def inspect_metrics_templates(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST5: Metrics & Templates ===")

    st, data = await state.call(
        client,
        "hmc_get_pcm_preferences",
        category="ManagedSystem",
        resource_name_or_uuid=context.system_name,
    )
    state.record_expected_or_real(
        5,
        "hmc_get_pcm_preferences",
        st,
        data,
        expected_fail_substrings=["PCM", "406", "403"],
        skip_reason="PCM not licensed on this HMC (expected)",
    )
    if st == "PASS":
        context.lp3_baseline["pcm_prefs"] = data

    st, data = await state.call(
        client,
        "hmc_processed_metric_links",
        category="ManagedSystem",
        resource_name_or_uuid=context.system_name,
        start_ts="2026-01-01T00:00:00.000Z",
    )
    state.record_expected_or_real(
        5,
        "hmc_processed_metrics (links)",
        st,
        data,
        expected_fail_substrings=["PCM", "406", "403"],
        skip_reason="PCM not licensed on this HMC (expected)",
    )

    st, data = await state.call(
        client,
        "hmc_aggregated_metric_links",
        category="ManagedSystem",
        resource_name_or_uuid=context.system_name,
        start_ts="2026-01-01T00:00:00.000Z",
    )
    state.record_expected_or_real(
        5,
        "hmc_aggregated_metrics (links)",
        st,
        data,
        expected_fail_substrings=["PCM", "406", "403"],
        skip_reason="PCM not licensed on this HMC (expected)",
    )

    st, data = await state.call(client, "hmc_list_partition_templates")
    state.record_expected_or_real(
        5,
        "hmc_list_partition_templates",
        st,
        data,
        expected_fail_substrings=["406", "template"],
        skip_reason="Partition templates not licensed on this HMC (expected)",
    )


# ---------------------------------------------------------------------------
# ST6 — User Inventory
# ---------------------------------------------------------------------------


async def inventory_users(client: Client, state: RunState) -> None:
    print("\n=== ST6: User Inventory ===")

    st, data = await state.call(client, "hmc_list_users")
    state.record_expected_or_real(
        6,
        "hmc_list_users",
        st,
        data,
        expected_fail_substrings=["REST000E", "400"],
        skip_reason="HmcUser REST endpoint not supported on this HMC (expected)",
    )


# ---------------------------------------------------------------------------
# ST7 — CLI Escape Hatch
# ---------------------------------------------------------------------------


async def exercise_cli_escape_hatch(client: Client, state: RunState) -> None:
    print("\n=== ST7: CLI Escape Hatch ===")

    st, data = await state.call(client, "hmc_run_command", cmd="lshmc -V")
    state.record(7, "hmc_run_command (lshmc -V)", st, data)

    st, data = await state.call(client, "hmc_run_command", cmd="lssyscfg -r sys")
    state.record(7, "hmc_run_command (lssyscfg -r sys)", st, data)


# ---------------------------------------------------------------------------
# ST8 — LPAR Lifecycle (scratch LPAR ltczz386-lp3-test)
# ---------------------------------------------------------------------------
