"""Storage inventory scenarios for the live HMC test harness."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastmcp import Client

from .results import entries
from .results import resource as get_resource

if TYPE_CHECKING:
    from live_test_runner import RunState

# ---------------------------------------------------------------------------
# ST3 — Storage & SSP Inventory
# ---------------------------------------------------------------------------


def _virtual_disks(resource: dict[str, Any]) -> list[dict[str, Any]]:
    disks = resource.get("VirtualDisks") or resource.get("virtual_disks") or []
    if isinstance(disks, dict):
        disks = disks.get("VirtualDisk") or []
    if isinstance(disks, dict):
        return [disks]
    return disks if isinstance(disks, list) else []


def _capture_disk_capacity(state: RunState, disk: dict[str, Any]) -> bool:
    context = state.context
    resource = get_resource(disk)
    if resource.get("DiskName") != context.vdisk_name:
        return False
    raw = resource.get("DiskCapacity") or resource.get("disk_capacity")
    try:
        gib = int(float(raw))
        if gib <= 0:
            raise ValueError("capacity must be positive")
        context.vdisk_size_mib = gib * 1024
    except (TypeError, ValueError):
        context.vdisk_size_mib = None
        state.record(
            3,
            "parse virtual disk capacity",
            "FAIL",
            f"Disk {context.vdisk_name!r} has invalid DiskCapacity {raw!r}; "
            "storage mutation will be skipped",
        )
    return True


def _capture_volume_group(state: RunState, data: Any) -> None:
    context = state.context
    for volume_group in entries(data):
        resource = get_resource(volume_group)
        found_target = any(
            _capture_disk_capacity(state, disk) for disk in _virtual_disks(resource)
        )
        if found_target or not context.vg_uuid:
            context.vg_uuid = volume_group.get("UUID") or volume_group.get("uuid")
            context.vdisk_vg_name = (
                resource.get("GroupName") or resource.get("group_name") or ""
            )
        if found_target:
            break


async def _discover_volume_group(client: Client, state: RunState) -> None:
    context = state.context
    if not context.vios_uuid:
        state.skip(
            3,
            "hmc_list_volume_groups",
            "no VIOS UUID in context (ST0/ST1 failed)",
        )
        return
    st, data = await state.call(
        client, "hmc_list_volume_groups", vios_name_or_uuid=context.vios_uuid
    )
    state.record(3, "hmc_list_volume_groups", st, data)
    if st == "PASS":
        _capture_volume_group(state, data)
    print(f"  VG UUID: {context.vg_uuid}  vdisk_size_mib: {context.vdisk_size_mib}")


async def _record_storage_collections(client: Client, state: RunState) -> None:
    context = state.context
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


async def inventory_storage(client: Client, state: RunState) -> None:
    print("\n=== ST3: Storage & SSP Inventory ===")
    await _discover_volume_group(client, state)
    await _record_storage_collections(client, state)
