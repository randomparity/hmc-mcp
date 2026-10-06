"""Connectivity and core resource inventory for the live HMC test harness."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp import Client

from hmcpctl.xmlutil import console_version

from .observation import Assertion, ExpectedOutcome
from .results import entries
from .results import resource as get_resource

if TYPE_CHECKING:
    from live_test_runner import RunState

# Known HMC firmware limitation: list_systems and list_console fail with
# HTTP 500 on hardware where a null VirtualPersistentMemoryVolume/Uuid field
# cannot be serialized. Direct-object lookups (get_system, get_lpar) work.
_FIRMWARE_INVENTORY_500 = ExpectedOutcome(
    operation="system.list",
    variant="firmware-inventory-serialization",
    reason="HMC firmware cannot serialize a null hardware property (HTTP 500 — known firmware limitation on this hardware; direct-object lookups still work)",
    error_codes=frozenset({"VirtualPersistentMemoryVolume"}),
)
_FIRMWARE_CAPACITY_500 = ExpectedOutcome(
    operation="capacity.report",
    variant="firmware-inventory-serialization",
    reason=_FIRMWARE_INVENTORY_500.reason,
    error_codes=_FIRMWARE_INVENTORY_500.error_codes,
)
_FIRMWARE_PLACEMENT_500 = ExpectedOutcome(
    operation="placement.find",
    variant="firmware-inventory-serialization",
    reason=_FIRMWARE_INVENTORY_500.reason,
    error_codes=_FIRMWARE_INVENTORY_500.error_codes,
)

# ---------------------------------------------------------------------------
# ST1 — Connectivity & Inventory
# ---------------------------------------------------------------------------


async def _discover_console(client: Client, state: RunState) -> object:
    st, data = await state.call(client, "hmc_get_console_info")
    console_uuid = None
    if st == "PASS" and isinstance(data, dict):
        console_uuid = data.get("uuid") or data.get("UUID")
    # console.info can fail with HTTP 500 on the same firmware bug that breaks
    # list_systems; record the assertion so the gap is visible in the maturity catalog.
    state.record_verified(
        1,
        "hmc_get_console_info",
        operation="console.info",
        scenario="st1-console-identity",
        assertions=[Assertion("console-uuid-present", bool(console_uuid))],
        cleanup="not-required",
        data=data,
    )
    if console_uuid:
        state.artifacts.console_uuid = console_uuid
    return data


async def _discover_system(client: Client, state: RunState) -> None:
    config = state.config
    artifacts = state.artifacts

    st, data = await state.call(
        client, "hmc_list_systems", expected=[_FIRMWARE_INVENTORY_500]
    )
    matched_uuid = None
    if st == "PASS":
        for e in entries(data):
            resource = get_resource(e)
            if config.system_name.lower() in (resource.get("SystemName") or "").lower():
                matched_uuid = e.get("UUID")
                break
        if not matched_uuid:
            first = entries(data)
            if first:
                matched_uuid = first[0].get("UUID")
    # hmc_list_systems can fail with HTTP 500 on hardware with a firmware bug
    # that cannot serialize null VirtualPersistentMemoryVolume/Uuid fields.
    state.record_with_expected(
        1,
        "hmc_list_systems",
        st,
        data,
        [_FIRMWARE_INVENTORY_500],
    )
    if matched_uuid:
        artifacts.system_uuid = matched_uuid

    st, data = await state.call(
        client, "hmc_get_system", system_name_or_uuid=config.system_name
    )
    system_uuid = None
    if st == "PASS" and isinstance(data, dict):
        system_uuid = data.get("UUID") or data.get("uuid")
    state.record_verified(
        1,
        "hmc_get_system",
        operation="system.get",
        scenario="st1-system-inventory",
        assertions=[
            Assertion("system-uuid-present", bool(system_uuid)),
        ],
        cleanup="not-required",
        data=data,
    )
    # Fall back: extract system UUID from the single-system lookup if the list
    # returned empty (e.g. HMC firmware bug on unfiltered ManagedSystem feed)
    if system_uuid and not artifacts.system_uuid:
        artifacts.system_uuid = system_uuid
    print(f"  System UUID: {artifacts.system_uuid}")


async def _discover_partitions(client: Client, state: RunState) -> None:
    config = state.config
    artifacts = state.artifacts

    st, data = await state.call(client, "hmc_list_lpars")
    state.record_verified(
        1,
        "hmc_list_lpars",
        operation="lpar.list",
        scenario="st1-lpar-inventory",
        assertions=[
            Assertion("lpar-list-non-empty", bool(st == "PASS" and entries(data))),
        ],
        cleanup="not-required",
        data=data,
    )

    st, data = await state.call(
        client, "hmc_get_lpar", lpar_name_or_uuid=config.lp3_name
    )
    lpar_uuid = None
    if st == "PASS" and isinstance(data, dict):
        lpar_uuid = data.get("uuid") or data.get("UUID")
    state.record_verified(
        1,
        "hmc_get_lpar",
        operation="lpar.get",
        scenario="st1-lpar-inventory",
        assertions=[
            Assertion("lpar-uuid-present", bool(lpar_uuid)),
        ],
        cleanup="not-required",
        data=data,
    )
    if lpar_uuid and not artifacts.lp3_uuid:
        artifacts.lp3_uuid = lpar_uuid


async def _discover_vios(client: Client, state: RunState) -> None:
    config = state.config
    artifacts = state.artifacts

    st, data = await state.call(
        client, "hmc_list_vios", system_name_or_uuid=config.system_name
    )
    vios_uuid = None
    vios_partition_id = None
    if st == "PASS":
        for e in entries(data):
            resource = get_resource(e)
            uuid = e.get("UUID") or e.get("uuid")
            pid = resource.get("PartitionID") or resource.get("partition_id")
            if uuid:
                vios_uuid = uuid
                vios_partition_id = int(pid) if pid is not None else None
                break
    state.record_verified(
        1,
        "hmc_list_vios",
        operation="vios.list",
        scenario="st1-vios-inventory",
        assertions=[
            Assertion("vios-list-non-empty", bool(st == "PASS" and entries(data))),
            Assertion("vios-uuid-present", bool(vios_uuid)),
        ],
        cleanup="not-required",
        data=data,
    )
    if vios_uuid and not artifacts.vios_uuid:
        artifacts.vios_uuid = vios_uuid
        artifacts.vios_partition_id = vios_partition_id
    print(
        f"  VIOS UUID: {artifacts.vios_uuid}  PartitionID: {artifacts.vios_partition_id}"
    )


async def _probe_capacity_and_resources(client: Client, state: RunState) -> None:
    config = state.config

    st, data = await state.call(
        client, "hmc_capacity_report", expected=[_FIRMWARE_CAPACITY_500]
    )
    # capacity.report uses list_systems internally; same firmware 500 applies.
    state.record_with_expected(
        1,
        "hmc_capacity_report",
        st,
        data,
        [_FIRMWARE_CAPACITY_500],
    )

    st, data = await state.call(
        client,
        "hmc_find_placement",
        desired_memory_mib=config.placement_memory_mib,
        expected=[_FIRMWARE_PLACEMENT_500],
    )
    # find_placement also uses list_systems; firmware 500 applies here too.
    state.record_with_expected(
        1,
        "hmc_find_placement",
        st,
        data,
        [_FIRMWARE_PLACEMENT_500],
    )

    st, data = await state.call(
        client, "hmc_get_system", system_name_or_uuid=config.system_name
    )
    state.record(1, "hmc_get_system (capacity context)", st, data)

    st, data = await state.call(
        client, "hmc_list_resources", resource_type="LogicalPartition"
    )
    state.record_verified(
        1,
        "hmc_list_resources",
        operation="console.list_resources",
        scenario="st1-resource-inventory",
        assertions=[
            Assertion(
                "resource-list-non-empty",
                bool(st == "PASS" and entries(data)),
            ),
        ],
        cleanup="not-required",
        data=data,
    )


async def _record_inventory_summaries(client: Client, state: RunState) -> None:
    config = state.config

    st, data = await state.call(
        client, "hmc_system_summary", system_name_or_uuid=config.system_name
    )
    # SystemSummary is a dataclass, not a dict; a non-None uuid confirms a real record.
    has_summary = (
        st == "PASS"
        and data is not None
        and (
            (isinstance(data, dict) and (data.get("uuid") or data.get("UUID")))
            or getattr(data, "uuid", None) is not None
        )
    )
    state.record_verified(
        1,
        "hmc_system_summary",
        operation="system.summary",
        scenario="st1-system-inventory",
        assertions=[
            Assertion("system-summary-returned", has_summary),
        ],
        cleanup="not-required",
        data=data,
    )

    st, data = await state.call(
        client, "hmc_lpar_summary", lpar_name_or_uuid=config.lp3_name
    )
    # LparSummary is a dataclass, not a dict; a non-None uuid confirms a real record.
    has_lpar_summary = (
        st == "PASS"
        and data is not None
        and (
            (isinstance(data, dict) and (data.get("uuid") or data.get("UUID")))
            or getattr(data, "uuid", None) is not None
        )
    )
    state.record_verified(
        1,
        "hmc_lpar_summary",
        operation="lpar.summary",
        scenario="st1-lpar-inventory",
        assertions=[
            Assertion("lpar-summary-returned", has_lpar_summary),
        ],
        cleanup="not-required",
        data=data,
    )


# PlatformUpdate needs HMC 11.1.1111 (docs/refs/hmc-rest-api-p11/jobs/managedsystem-jobs/
# 065-platformupdate_managedsystem-job.md:12). The name is absent on purpose: an HMC past
# the gate would still stop at the name lookup, before any PlatformUpdate PUT.
_PLATFORM_UPDATE_MINIMUM = (11, 1, 1111)
_ABSENT_SYSTEM = "hmcpctl-live-absent-system"
_PLATFORM_UPDATE_CHECK = "hmc_update_firmware (pre-11.1.1111 refusal)"


async def _check_platform_update_refusal(
    client: Client, state: RunState, console: object
) -> None:
    """A pre-11.1.1111 HMC refuses PlatformUpdate before any lookup; a non-promoting check."""
    resource = console.get("Resource") if isinstance(console, dict) else None
    version = console_version(resource) if isinstance(resource, dict) else None
    if version is None or version >= _PLATFORM_UPDATE_MINIMUM:
        state.skip(
            1,
            _PLATFORM_UPDATE_CHECK,
            "runs only on an HMC whose version reads below 11.1.1111",
        )
        return
    status, data = await state.call(
        client,
        "hmc_update_firmware",
        system_name_or_uuid=_ABSENT_SYSTEM,
        platform_update={
            "SystemFirmwareUpdate": {"UpdateType": "Update", "UpdateOrder": 1}
        },
    )
    text = str(data)
    refused = (
        status == "FAIL"
        and "requires HMC 11.1.1111" in text
        and "below the minimum" in text
    )
    state.record(
        1,
        _PLATFORM_UPDATE_CHECK,
        "PASS" if refused else "FAIL",
        data,
        "refused before resolving the system"
        if refused
        else "expected the 11.1.1111 version refusal",
    )


async def inventory_connectivity(client: Client, state: RunState) -> None:
    print("\n=== ST1: Connectivity & Inventory ===")
    console = await _discover_console(client, state)
    await _discover_system(client, state)
    await _discover_partitions(client, state)
    await _discover_vios(client, state)
    await _probe_capacity_and_resources(client, state)
    await _record_inventory_summaries(client, state)
    await _check_platform_update_refusal(client, state, console)
