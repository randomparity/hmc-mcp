"""Metrics and job-monitoring scenarios for the live HMC test harness."""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from fastmcp import Client

from hmcpctl.jobs import SUCCESSFUL_JOB_STATUSES, JobOutcome, job_outcome

from .observation import Assertion, ExpectedOutcome

if TYPE_CHECKING:
    from live_test_runner import RunState


def _pcm_authority(operation: str) -> ExpectedOutcome:
    """A PCM read the connecting user has no authority for: a prerequisite, not a pass.

    Only the 403 is declared. A 406 is the media-type defect #1202 and #634 fixed,
    and must fail if it recurs; a bare `PCM` token would match any message that
    names PCM, including a real failure.
    """
    return ExpectedOutcome(
        operation=operation,
        variant="pcm-authority",
        reason="the connecting user lacks PCM authority (HTTP 403)",
        error_codes=frozenset({"403"}),
    )


# Module-level names: the runner's startup validator reads `expected=[...]` as a
# literal list of module declarations.
_PREFERENCES_AUTHORITY = _pcm_authority("pcm.get_preferences")
_PROCESSED_LINKS_AUTHORITY = _pcm_authority("metrics.processed_links")
_AGGREGATED_LINKS_AUTHORITY = _pcm_authority("metrics.aggregated_links")
_PROCESSED_DATA_AUTHORITY = _pcm_authority("metrics.processed")
_AGGREGATED_DATA_AUTHORITY = _pcm_authority("metrics.aggregated")

#: The five managed-system collection flags, as read and as the set tool names them.
PCM_FLAGS = (
    ("LongTermMonitorEnabled", "long_term_monitor"),
    ("AggregationEnabled", "aggregation"),
    ("ShortTermMonitorEnabled", "short_term_monitor"),
    ("ComputeLTMEnabled", "compute_ltm"),
    ("EnergyMonitorEnabled", "energy_monitor"),
)

_ST5_SCENARIO = "st5-pcm-template-reads"
_ST38_SCENARIO = "st38-pcm-preference-round-trip"
#: ST5's metric window: processed metrics are retained for about two hours.
_METRIC_WINDOW = timedelta(hours=2)
_NO_SAMPLE = (
    "no {kind} sample in the last two hours; needs LongTermMonitorEnabled, "
    "AggregationEnabled and collection time"
)

#: ST12's job scenario: both tools are asserted against the same postconditions.
_JOB_SCENARIO = "st12-job-inspection"


def _as_outcome(data: Any) -> JobOutcome | None:
    """Normalize a job tool's result, whatever shape FastMCP served it in.

    ``hmc_wait_for_job`` is annotated ``-> JobOutcome``, so FastMCP serves an
    unwrapped output schema and ``result.data`` arrives as a generated pydantic
    model rather than a mapping. Reading it by field name covers both shapes;
    an ``isinstance(data, dict)`` test would silently reject every real run.
    """
    names = [field.name for field in fields(JobOutcome)]
    if isinstance(data, dict):
        source: Any = data
    elif all(hasattr(data, name) for name in names):
        source = {name: getattr(data, name) for name in names}
    else:
        return None
    try:
        return JobOutcome(**{name: source[name] for name in names})
    except (KeyError, TypeError):
        return None


def _job_assertions(outcome: JobOutcome | None, job_uuid: str) -> list[Assertion]:
    """The postconditions a job inspection must hold to count as evidence."""
    return [
        Assertion("job-found", bool(outcome and outcome.found)),
        Assertion("job-identity-matches", bool(outcome and outcome.job_id == job_uuid)),
        Assertion(
            "job-status-successful",
            bool(outcome and outcome.status in SUCCESSFUL_JOB_STATUSES),
        ),
    ]


# ---------------------------------------------------------------------------
# ST12 — Job Monitoring
# ---------------------------------------------------------------------------


async def inspect_metrics_jobs(client: Client, state: RunState) -> None:
    """Inspect a captured job. PCM preferences are left alone: the pcm arm owns them."""
    artifacts = state.artifacts
    print("\n=== ST12: Job Monitoring ===")

    job_uuid = artifacts.job_uuid_sample
    if job_uuid:
        _, data = await state.call(client, "hmc_get_job", job_id=job_uuid)
        state.record_verified(
            12,
            "hmc_get_job",
            operation="job.get",
            scenario=_JOB_SCENARIO,
            assertions=_job_assertions(
                job_outcome(job_uuid, data if isinstance(data, dict) else None),
                job_uuid,
            ),
            cleanup="not-required",
            data=data,
        )

        _, data = await state.call(
            client,
            "hmc_wait_for_job",
            job_id=job_uuid,
            timeout_seconds=10,
            poll_interval=2,
        )
        state.record_verified(
            12,
            "hmc_wait_for_job",
            operation="job.wait",
            scenario=_JOB_SCENARIO,
            assertions=_job_assertions(_as_outcome(data), job_uuid),
            cleanup="not-required",
            data=data,
        )
    else:
        state.skip(12, "hmc_get_job", "no job UUID captured (ST8 may have failed)")
        state.skip(12, "hmc_wait_for_job", "no job UUID")


def _preference_assertions(data: Any) -> list[Assertion]:
    flags = data if isinstance(data, dict) else {}
    return [
        Assertion(
            "five-flags-boolean",
            all(isinstance(flags.get(name), bool) for name, _ in PCM_FLAGS),
        )
    ]


def _link_assertions(links: Any, system_uuid: str) -> list[Assertion]:
    # The reference names each document `<Category>_<uuid>_..._<interval>.json`.
    return [
        Assertion(
            "links-name-system",
            isinstance(links, list)
            and all(
                isinstance(link, dict)
                and system_uuid.lower() in str(link.get("link", "")).lower()
                for link in links
            ),
        )
    ]


def _document_assertions(document: Any, system_uuid: str) -> list[Assertion]:
    found = document.get("systemUtil") if isinstance(document, dict) else None
    util: dict[str, Any] = found if isinstance(found, dict) else {}
    info: dict[str, Any] = (
        util["utilInfo"] if isinstance(util.get("utilInfo"), dict) else {}
    )
    return [
        Assertion(
            "document-names-system",
            str(info.get("uuid", "")).lower() == system_uuid.lower(),
        ),
        Assertion("samples-present", bool(util.get("utilSamples"))),
    ]


def _template_list_assertions(status: str, items: Any) -> list[Assertion]:
    if status != "PASS":
        return [Assertion("read-succeeded", False)]
    # The library feed lists summaries (tests/fixtures/live/rest-templates-feed.json).
    return [
        Assertion(
            "entries-are-template-summaries",
            isinstance(items, list)
            and bool(items)
            and all(
                isinstance(item, dict)
                and item.get("ResourceType") == "PartitionTemplateSummary"
                and bool(item.get("UUID"))
                for item in items
            ),
        )
    ]


def _template_get_assertions(
    status: str, item: Any, template_uuid: str
) -> list[Assertion]:
    if status != "PASS":
        return [Assertion("read-succeeded", False)]
    uuid = item.get("UUID") if isinstance(item, dict) else None
    return [
        Assertion(
            "template-identity-matches",
            str(uuid or "").lower() == template_uuid.lower(),
        )
    ]


def _first_template_uuid(items: Any) -> str | None:
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("UUID"), str):
            return item["UUID"]
    return None


async def _system_uuid(client: Client, state: RunState) -> str | None:
    """ST1's system UUID, or one read of it when ST5 runs on its own."""
    if state.artifacts.system_uuid:
        return state.artifacts.system_uuid
    st, data = await state.call(
        client, "hmc_get_system", system_name_or_uuid=state.config.system_name
    )
    state.record(5, "hmc_get_system (ST5 system UUID)", st, data)
    found = data.get("UUID") or data.get("uuid") if isinstance(data, dict) else None
    if st == "PASS" and isinstance(found, str) and found:
        state.artifacts.system_uuid = found
        return found
    return None


# ---------------------------------------------------------------------------
# ST5 — Metrics & Templates
# ---------------------------------------------------------------------------


async def inspect_metrics_templates(client: Client, state: RunState) -> None:
    """Read PCM preferences, metric feeds and documents, and the template library."""
    config = state.config
    print("\n=== ST5: Metrics & Templates ===")

    st, data = await state.call(
        client,
        "hmc_get_pcm_preferences",
        expected=[_PREFERENCES_AUTHORITY],
        category="ManagedSystem",
        resource_name_or_uuid=config.system_name,
    )
    if st == "PASS":
        state.artifacts.lp3_baseline["pcm_prefs"] = data
        state.record_verified(
            5,
            "hmc_get_pcm_preferences",
            operation="pcm.get_preferences",
            scenario=_ST5_SCENARIO,
            assertions=_preference_assertions(data),
            cleanup="not-required",
            data=data,
        )
    else:
        state.record_with_expected(
            5, "hmc_get_pcm_preferences", st, data, [_PREFERENCES_AUTHORITY]
        )

    system_uuid = await _system_uuid(client, state)
    if system_uuid is None:
        for tool in (
            "hmc_processed_metric_links",
            "hmc_processed_metrics",
            "hmc_aggregated_metric_links",
            "hmc_aggregated_metrics",
        ):
            state.skip(5, tool, "no managed-system UUID to check the metrics against")
    else:
        start_ts = (datetime.now(UTC) - _METRIC_WINDOW).strftime("%Y-%m-%dT%H:%M:%SZ")
        await _inspect_processed_metrics(client, state, system_uuid, start_ts)
        await _inspect_aggregated_metrics(client, state, system_uuid, start_ts)

    await _inspect_templates(client, state)


async def _inspect_processed_metrics(
    client: Client, state: RunState, system_uuid: str, start_ts: str
) -> None:
    """The processed feed, then its newest document when the feed lists one."""
    system = state.config.system_name
    st, links = await state.call(
        client,
        "hmc_processed_metric_links",
        expected=[_PROCESSED_LINKS_AUTHORITY],
        category="ManagedSystem",
        resource_name_or_uuid=system,
        start_ts=start_ts,
    )
    if st == "PASS" and links:
        state.record_verified(
            5,
            "hmc_processed_metric_links",
            operation="metrics.processed_links",
            scenario=_ST5_SCENARIO,
            assertions=_link_assertions(links, system_uuid),
            cleanup="not-required",
            data=links,
        )
    elif st == "PASS":
        state.skip(5, "hmc_processed_metric_links", _NO_SAMPLE.format(kind="processed"))
    else:
        state.record_with_expected(
            5, "hmc_processed_metric_links", st, links, [_PROCESSED_LINKS_AUTHORITY]
        )
    if not (st == "PASS" and links):
        state.skip(5, "hmc_processed_metrics", "no processed metric links to fetch")
        return

    st, document = await state.call(
        client,
        "hmc_processed_metrics",
        expected=[_PROCESSED_DATA_AUTHORITY],
        category="ManagedSystem",
        resource_name_or_uuid=system,
        start_ts=start_ts,
    )
    if st == "PASS" and document:
        state.record_verified(
            5,
            "hmc_processed_metrics",
            operation="metrics.processed",
            scenario=_ST5_SCENARIO,
            assertions=_document_assertions(document, system_uuid),
            cleanup="not-required",
            data=document,
        )
    elif st == "PASS":
        state.skip(5, "hmc_processed_metrics", _NO_SAMPLE.format(kind="processed"))
    else:
        state.record_with_expected(
            5, "hmc_processed_metrics", st, document, [_PROCESSED_DATA_AUTHORITY]
        )


async def _inspect_aggregated_metrics(
    client: Client, state: RunState, system_uuid: str, start_ts: str
) -> None:
    """The aggregated feed, then its newest document when the feed lists one."""
    system = state.config.system_name
    st, links = await state.call(
        client,
        "hmc_aggregated_metric_links",
        expected=[_AGGREGATED_LINKS_AUTHORITY],
        category="ManagedSystem",
        resource_name_or_uuid=system,
        start_ts=start_ts,
    )
    if st == "PASS" and links:
        state.record_verified(
            5,
            "hmc_aggregated_metric_links",
            operation="metrics.aggregated_links",
            scenario=_ST5_SCENARIO,
            assertions=_link_assertions(links, system_uuid),
            cleanup="not-required",
            data=links,
        )
    elif st == "PASS":
        state.skip(
            5, "hmc_aggregated_metric_links", _NO_SAMPLE.format(kind="aggregated")
        )
    else:
        state.record_with_expected(
            5, "hmc_aggregated_metric_links", st, links, [_AGGREGATED_LINKS_AUTHORITY]
        )
    if not (st == "PASS" and links):
        state.skip(5, "hmc_aggregated_metrics", "no aggregated metric links to fetch")
        return

    st, document = await state.call(
        client,
        "hmc_aggregated_metrics",
        expected=[_AGGREGATED_DATA_AUTHORITY],
        category="ManagedSystem",
        resource_name_or_uuid=system,
        start_ts=start_ts,
    )
    if st == "PASS" and document:
        state.record_verified(
            5,
            "hmc_aggregated_metrics",
            operation="metrics.aggregated",
            scenario=_ST5_SCENARIO,
            assertions=_document_assertions(document, system_uuid),
            cleanup="not-required",
            data=document,
        )
    elif st == "PASS":
        state.skip(5, "hmc_aggregated_metrics", _NO_SAMPLE.format(kind="aggregated"))
    else:
        state.record_with_expected(
            5, "hmc_aggregated_metrics", st, document, [_AGGREGATED_DATA_AUTHORITY]
        )


async def _inspect_templates(client: Client, state: RunState) -> None:
    """List the template library, then read the first listed template back."""
    st, items = await state.call(client, "hmc_list_partition_templates")
    state.record_verified(
        5,
        "hmc_list_partition_templates",
        operation="template.list",
        scenario=_ST5_SCENARIO,
        assertions=_template_list_assertions(st, items),
        cleanup="not-required",
        data=items,
    )
    template_uuid = _first_template_uuid(items) if st == "PASS" else None
    if template_uuid is None:
        state.skip(5, "hmc_get_partition_template", "no partition template listed")
        return
    st, item = await state.call(
        client, "hmc_get_partition_template", template_uuid=template_uuid
    )
    state.record_verified(
        5,
        "hmc_get_partition_template",
        operation="template.get",
        scenario=_ST5_SCENARIO,
        assertions=_template_get_assertions(st, item, template_uuid),
        cleanup="not-required",
        data=item,
    )


# ---------------------------------------------------------------------------
# ST38 — PCM preference round trip (the pcm arm)
# ---------------------------------------------------------------------------


async def _read_preferences(
    client: Client, state: RunState, label: str
) -> dict[str, Any]:
    """Read the five flags, recorded as a non-promoting row named for its step."""
    st, data = await state.call(
        client,
        "hmc_get_pcm_preferences",
        category="ManagedSystem",
        resource_name_or_uuid=state.config.system_name,
    )
    state.record(38, f"hmc_get_pcm_preferences ({label})", st, data)
    source = data if st == "PASS" and isinstance(data, dict) else {}
    return {name: source.get(name) for name, _ in PCM_FLAGS}


def _round_trip_assertions(toggled: dict[str, bool], restored: bool) -> list[Assertion]:
    """One assertion per flag that read back flipped, and the final restore."""
    return [
        Assertion("long-term-monitor-toggled", toggled["LongTermMonitorEnabled"]),
        Assertion("aggregation-toggled", toggled["AggregationEnabled"]),
        Assertion("short-term-monitor-toggled", toggled["ShortTermMonitorEnabled"]),
        Assertion("compute-ltm-toggled", toggled["ComputeLTMEnabled"]),
        Assertion("energy-monitor-toggled", toggled["EnergyMonitorEnabled"]),
        Assertion("snapshot-restored", restored),
    ]


async def _set_preferences(
    client: Client, state: RunState, label: str, values: dict[str, bool]
) -> None:
    """Write the named flags (keyword -> value), recorded as a non-promoting row."""
    st, data = await state.call(
        client,
        "hmc_set_pcm_preferences",
        category="ManagedSystem",
        resource_name_or_uuid=state.config.system_name,
        long_term_monitor=values.get("long_term_monitor"),
        aggregation=values.get("aggregation"),
        short_term_monitor=values.get("short_term_monitor"),
        compute_ltm=values.get("compute_ltm"),
        energy_monitor=values.get("energy_monitor"),
    )
    state.record(38, f"hmc_set_pcm_preferences ({label})", st, data)


async def exercise_pcm_preferences(client: Client, state: RunState) -> None:
    """Toggle each managed-system PCM flag and restore all five to the first read.

    The snapshot read is a results row before the first write, so a finished or
    interrupted results document carries the values to restore by hand
    (docs/live-testing.md). Each restore writes all five values: the HMC couples
    the flags (enabling aggregation also enables long-term monitoring and, where
    the system supports it, energy monitoring). A restore that does not read back
    as the snapshot stops the loop before the next toggle.
    """
    print("\n=== ST38: PCM Preference Round Trip ===")
    if state.group != "pcm":
        state.skip(38, "hmc_set_pcm_preferences", "runs only in the pcm arm")
        return
    snapshot = await _read_preferences(client, state, "snapshot")
    if not all(isinstance(value, bool) for value in snapshot.values()):
        state.skip(
            38,
            "hmc_set_pcm_preferences",
            "the snapshot read did not return five boolean flags; nothing changed",
        )
        return
    restore = {keyword: bool(snapshot[name]) for name, keyword in PCM_FLAGS}
    toggled = {name: False for name, _ in PCM_FLAGS}
    for name, keyword in PCM_FLAGS:
        flipped = not snapshot[name]
        await _set_preferences(client, state, f"{keyword} toggle", {keyword: flipped})
        after = await _read_preferences(client, state, f"{keyword} toggled")
        toggled[name] = after[name] is flipped
        await _set_preferences(client, state, f"{keyword} restore", restore)
        if await _read_preferences(client, state, f"{keyword} restored") != snapshot:
            break  # widen nothing further: the final read reports the deviation
    final = await _read_preferences(client, state, "final")
    restored = final == snapshot
    state.record_verified(
        38,
        "hmc_set_pcm_preferences",
        operation="pcm.set_preferences",
        scenario=_ST38_SCENARIO,
        assertions=_round_trip_assertions(toggled, restored),
        cleanup="passed" if restored else "failed",
        data=final,
    )
    if not restored:
        state.record(
            38,
            "hmc_set_pcm_preferences (MANUAL RECOVERY REQUIRED)",
            "FAIL",
            snapshot,
            "restore the snapshot: "
            + ", ".join(f"{name}={value}" for name, value in snapshot.items()),
        )
