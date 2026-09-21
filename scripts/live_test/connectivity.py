"""Connectivity and core resource inventory for the live HMC test harness."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp import Client

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

# Known HMC version limitation: global Job feed is not supported on older HMC
# releases (REST000E). Per-job polling via hmc_get_job still works.
_GLOBAL_JOB_LISTING_UNSUPPORTED = ExpectedOutcome(
    operation="job.list",
    variant="global-job-feed",
    reason="HMC version does not support global Job listing (REST000E — use hmc_get_job with a submission link instead)",
    error_codes=frozenset({"REST000E"}),
)

# ---------------------------------------------------------------------------
# ST1 — Connectivity & Inventory
# ---------------------------------------------------------------------------


async def _discover_console(client: Client, state: RunState) -> None:
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
        assertions=[
            Assertion("console-uuid-present", bool(console_uuid))
        ],
        cleanup="not-required",
        data=data,
    )
    if console_uuid:
        state.artifacts.console_uuid = console_uuid


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
        client, "hmc_find_placement", desired_memory_mib=config.placement_memory_mib,
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


async def _sample_recent_job(client: Client, state: RunState) -> None:
    artifacts = state.artifacts

    st, data = await state.call(
        client, "hmc_list_recent_jobs", limit=10, expected=[_GLOBAL_JOB_LISTING_UNSUPPORTED]
    )
    job_uuid = None
    if st == "PASS":
        for e in entries(data):
            if isinstance(e, dict) and e.get("type") != "error":
                job_uuid = e.get("UUID") or e.get("uuid")
                break
    # hmc_list_recent_jobs is not supported on older HMC firmware (HTTP 400).
    # Use record_with_expected so the version gap is recorded rather than counted
    # as a real failure; the job_uuid_sample will still be set from ST8 jobs.
    state.record_with_expected(
        1,
        "hmc_list_recent_jobs",
        st,
        data,
        [_GLOBAL_JOB_LISTING_UNSUPPORTED],
    )
    if job_uuid:
        artifacts.job_uuid_sample = job_uuid


async def _record_inventory_summaries(client: Client, state: RunState) -> None:
    config = state.config

    st, data = await state.call(
        client, "hmc_system_summary", system_name_or_uuid=config.system_name
    )
    # SystemSummary is a dataclass, not a dict; a non-None uuid confirms a real record.
    has_summary = st == "PASS" and data is not None and (
        (isinstance(data, dict) and (data.get("uuid") or data.get("UUID")))
        or getattr(data, "uuid", None) is not None
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
    has_lpar_summary = st == "PASS" and data is not None and (
        (isinstance(data, dict) and (data.get("uuid") or data.get("UUID")))
        or getattr(data, "uuid", None) is not None
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


async def inventory_connectivity(client: Client, state: RunState) -> None:
    print("\n=== ST1: Connectivity & Inventory ===")
    await _discover_console(client, state)
    await _discover_system(client, state)
    await _discover_partitions(client, state)
    await _discover_vios(client, state)
    await _probe_capacity_and_resources(client, state)
    await _sample_recent_job(client, state)
    await _record_inventory_summaries(client, state)
