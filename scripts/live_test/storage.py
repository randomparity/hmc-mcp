"""Storage inventory scenarios for the live HMC test harness."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastmcp import Client

from .results import entries
from .results import resource as get_resource

if TYPE_CHECKING:
    from live_test_runner import RunState

# ---------------------------------------------------------------------------
# ST3 — Storage & SSP Inventory
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfiguredVolumeGroup:
    """The listed volume group named by ``LIVE_TEST_VDISK_VOLUME_GROUP_NAME``."""

    uuid: str
    resource: Mapping[str, Any]
    free_space_mib: int | None


def _free_space_mib(entry: Mapping[str, Any], resource: Mapping[str, Any]) -> int | None:
    """Convert the HMC's GiB free-space figure to MiB; None when unknown."""
    gib = entry.get("free_space_gib")
    if gib is None:
        gib = resource.get("FreeSpace")
    try:
        return None if gib is None else int(float(gib) * 1024)
    except (TypeError, ValueError):
        return None


def resolve_configured_volume_group(
    state: RunState, stage: int, data: object, dependents: Sequence[str]
) -> ConfiguredVolumeGroup | None:
    """Return the configured volume group from a listing, or SKIP ``dependents``.

    The VIOS lists volume groups in no fixed order, so selection is by name
    only; a missing group is never replaced by another one (issue #967).
    """
    artifacts = state.artifacts
    name = state.config.vdisk_volume_group_name
    for entry in entries(data):
        resource = get_resource(entry)
        uuid = entry.get("uuid") or entry.get("UUID")
        listed_name = entry.get("name") or resource.get("GroupName")
        if listed_name == name and isinstance(uuid, str) and uuid:
            artifacts.vg_uuid, artifacts.vdisk_vg_name = uuid, name
            return ConfiguredVolumeGroup(uuid, resource, _free_space_mib(entry, resource))
    artifacts.vg_uuid = artifacts.vdisk_vg_name = None
    for dependent in dependents:
        state.skip(stage, dependent, f"configured volume group {name!r} not listed")
    return None


def configured_vg_uuid(state: RunState) -> str | None:
    """Return ``vg_uuid`` only when the resolver recorded it for the configured group.

    A results document restored for a subset run can carry another group's UUID.
    """
    artifacts = state.artifacts
    if artifacts.vdisk_vg_name != state.config.vdisk_volume_group_name:
        return None
    return artifacts.vg_uuid


def _capture_volume_group(state: RunState, data: Any) -> None:
    resolve_configured_volume_group(state, 3, data, ("select configured volume group",))


async def _discover_volume_group(client: Client, state: RunState) -> None:
    artifacts = state.artifacts
    if not artifacts.vios_uuid:
        state.skip(
            3,
            "hmc_list_volume_groups",
            "no VIOS UUID in context (ST0/ST1 failed)",
        )
        return
    st, data = await state.call(
        client, "hmc_list_volume_groups", vios_name_or_uuid=artifacts.vios_uuid
    )
    state.record(3, "hmc_list_volume_groups", st, data)
    if st == "PASS":
        _capture_volume_group(state, data)
    print(f"  VG UUID: {artifacts.vg_uuid}")


async def _record_storage_collections(client: Client, state: RunState) -> None:
    config = state.config
    st, data = await state.call(client, "hmc_list_clusters")
    state.record(3, "hmc_list_clusters", st, data)

    st, data = await state.call(client, "hmc_list_shared_storage_pools")
    state.record(3, "hmc_list_shared_storage_pools", st, data)

    st, data = await state.call(
        client, "hmc_list_io_slots", system_name_or_uuid=config.system_name
    )
    state.record(3, "hmc_list_io_slots", st, data)

    st, data = await state.call(
        client, "hmc_list_memory_pools", system_name_or_uuid=config.system_name
    )
    state.record(3, "hmc_list_memory_pools", st, data)


async def inventory_storage(client: Client, state: RunState) -> None:
    print("\n=== ST3: Storage & SSP Inventory ===")
    await _discover_volume_group(client, state)
    await _record_storage_collections(client, state)
