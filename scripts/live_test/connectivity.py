"""Connectivity and core resource inventory for the live HMC test harness."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp import Client

from .results import entries
from .results import resource as get_resource

if TYPE_CHECKING:
    from live_test_runner import RunState

# ---------------------------------------------------------------------------
# ST1 — Connectivity & Inventory
# ---------------------------------------------------------------------------


async def _discover_console(client: Client, state: RunState) -> None:
    st, data = await state.call(client, "hmc_get_console_info")
    state.record(1, "hmc_get_console_info", st, data)
    if st == "PASS" and isinstance(data, dict):
        state.context.console_uuid = data.get("uuid") or data.get("UUID")


async def _discover_system(client: Client, state: RunState) -> None:
    context = state.context

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


async def _discover_partitions(client: Client, state: RunState) -> None:
    context = state.context

    st, data = await state.call(client, "hmc_list_lpars")
    state.record(1, "hmc_list_lpars (list)", st, data)

    st, data = await state.call(
        client, "hmc_get_lpar", lpar_name_or_uuid=context.lp3_name
    )
    state.record(1, "hmc_get_lpar (single lp3)", st, data)
    if st == "PASS" and isinstance(data, dict) and not context.lp3_uuid:
        context.lp3_uuid = data.get("uuid") or data.get("UUID")


async def _discover_vios(client: Client, state: RunState) -> None:
    context = state.context

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


async def _probe_capacity_and_resources(client: Client, state: RunState) -> None:
    context = state.context

    st, data = await state.call(client, "hmc_capacity_report")
    state.record(1, "hmc_capacity_report", st, data)

    st, data = await state.call(
        client, "hmc_find_placement", desired_memory_mib=context.placement_memory_mib
    )
    state.record(1, "hmc_find_placement", st, data)

    st, data = await state.call(
        client, "hmc_get_system", system_name_or_uuid=context.system_name
    )
    state.record(1, "hmc_get_system", st, data)

    st, data = await state.call(
        client, "hmc_list_resources", resource_type="LogicalPartition"
    )
    state.record(1, "hmc_list_resources", st, data)


async def _sample_recent_job(client: Client, state: RunState) -> None:
    context = state.context

    st, data = await state.call(client, "hmc_list_recent_jobs", limit=10)
    state.record(1, "hmc_list_recent_jobs", st, data)
    if st == "PASS":
        for e in entries(data):
            if isinstance(e, dict) and e.get("type") != "error":
                context.job_uuid_sample = e.get("UUID") or e.get("uuid")
                break


async def _record_inventory_summaries(client: Client, state: RunState) -> None:
    context = state.context

    st, data = await state.call(
        client, "hmc_system_summary", system_name_or_uuid=context.system_name
    )
    state.record(1, "hmc_system_summary", st, data)

    st, data = await state.call(
        client, "hmc_lpar_summary", lpar_name_or_uuid=context.lp3_name
    )
    state.record(1, "hmc_lpar_summary", st, data)


async def inventory_connectivity(client: Client, state: RunState) -> None:
    print("\n=== ST1: Connectivity & Inventory ===")
    await _discover_console(client, state)
    await _discover_system(client, state)
    await _discover_partitions(client, state)
    await _discover_vios(client, state)
    await _probe_capacity_and_resources(client, state)
    await _sample_recent_job(client, state)
    await _record_inventory_summaries(client, state)
