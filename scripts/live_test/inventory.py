"""Read-only inventory scenarios for the live HMC test harness."""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

from fastmcp import Client

from hmc_mcp.ssh.commands import build_filter

from .results import entries
from .results import resource as get_resource

if TYPE_CHECKING:
    from live_test_runner import RunState


async def _capture_lpar_properties(client: Client, state: RunState) -> None:
    context = state.context
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


async def _capture_adapter_topology(client: Client, state: RunState) -> None:
    context = state.context
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


async def _capture_vios_identity(client: Client, state: RunState) -> None:
    context = state.context
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


async def _capture_lpar_cli_dump(client: Client, state: RunState) -> None:
    context = state.context
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


def _print_baseline_summary(state: RunState) -> None:
    context = state.context
    print(f"  lp3 UUID: {context.lp3_uuid}")
    print(f"  VIOS UUID: {context.vios_uuid}  PartitionID: {context.vios_partition_id}")
    print(
        f"  lp3 PVID: {context.lp3_baseline.get('pvid')}  "
        f"vSCSI VIOS slot: {context.lp3_baseline.get('vios_slot')}"
    )
    print(f"  Baseline keys: {list(context.lp3_baseline.keys())}")


async def capture_lpar_baseline(client: Client, state: RunState) -> None:
    print("\n=== ST0: Capture ltczz386-lp3 Baseline ===")
    await _capture_lpar_properties(client, state)
    await _capture_adapter_topology(client, state)
    await _capture_vios_identity(client, state)
    await _capture_lpar_cli_dump(client, state)
    _print_baseline_summary(state)
