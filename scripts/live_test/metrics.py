"""Metrics and job-monitoring scenarios for the live HMC test harness."""

from __future__ import annotations

from dataclasses import fields
from typing import TYPE_CHECKING, Any

from fastmcp import Client

from hmc_mcp.jobs import SUCCESSFUL_JOB_STATUSES, JobOutcome, job_outcome

from .observation import Assertion, ExpectedOutcome
from .results import entries

if TYPE_CHECKING:
    from live_test_runner import RunState

_PCM_UNLICENSED = ExpectedOutcome(
    operation="pcm.get_preferences",
    variant="managed-system-pcm",
    reason="PCM not licensed on this HMC (expected)",
    error_codes=frozenset({"PCM", "406", "403"}),
)
_PROCESSED_UNLICENSED = ExpectedOutcome(
    operation="metrics.processed_links",
    variant="managed-system-pcm",
    reason=_PCM_UNLICENSED.reason,
    error_codes=_PCM_UNLICENSED.error_codes,
)
_AGGREGATED_UNLICENSED = ExpectedOutcome(
    operation="metrics.aggregated_links",
    variant="managed-system-pcm",
    reason=_PCM_UNLICENSED.reason,
    error_codes=_PCM_UNLICENSED.error_codes,
)
_TEMPLATES_UNLICENSED = ExpectedOutcome(
    operation="template.list",
    variant="partition-templates",
    reason="Partition templates not licensed on this HMC (expected)",
    # Both numbers, because matching is whole-token now: the substring this
    # replaced matched "template" inside "templates", and a declared literal
    # has to name the token the message actually carries.
    error_codes=frozenset({"406", "template", "templates"}),
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
        Assertion(
            "job-identity-matches", bool(outcome and outcome.job_id == job_uuid)
        ),
        Assertion(
            "job-status-successful",
            bool(outcome and outcome.status in SUCCESSFUL_JOB_STATUSES),
        ),
    ]


# ---------------------------------------------------------------------------
# ST12 — PCM Metrics & Job Monitoring
# ---------------------------------------------------------------------------


async def inspect_metrics_jobs(client: Client, state: RunState) -> None:
    config = state.config
    artifacts = state.artifacts
    print("\n=== ST12: PCM Metrics & Job Monitoring ===")

    st, data = await state.call(
        client,
        "hmc_get_pcm_preferences",
        expected=[_PCM_UNLICENSED],
        category="ManagedSystem",
        resource_name_or_uuid=config.system_name,
    )
    state.record_with_expected(
        12,
        "hmc_get_pcm_preferences",
        st,
        data,
        [_PCM_UNLICENSED],
    )
    current_ltm = None
    if st == "PASS" and isinstance(data, dict):
        if "long_term_monitor" in data:
            current_ltm = data["long_term_monitor"]
        else:
            current_ltm = data.get("LongTermMonitorEnabled")

    if current_ltm is not None:
        new_ltm = not bool(current_ltm)
        st, data = await state.call(
            client,
            "hmc_set_pcm_preferences",
            category="ManagedSystem",
            resource_name_or_uuid=config.system_name,
            long_term_monitor=new_ltm,
        )
        state.record(12, "hmc_set_pcm_preferences (toggle)", st, data)

        st, data = await state.call(
            client,
            "hmc_get_pcm_preferences",
            category="ManagedSystem",
            resource_name_or_uuid=config.system_name,
        )
        state.record(12, "hmc_get_pcm_preferences (verify)", st, data)

        st, data = await state.call(
            client,
            "hmc_set_pcm_preferences",
            category="ManagedSystem",
            resource_name_or_uuid=config.system_name,
            long_term_monitor=bool(current_ltm),
        )
        state.record(12, "hmc_set_pcm_preferences (restore)", st, data)
    else:
        state.skip(
            12,
            "hmc_set_pcm_preferences",
            "PCM not licensed/enabled on this HMC (expected)",
        )

    job_uuid = artifacts.job_uuid_sample
    if job_uuid:
        st, data = await state.call(client, "hmc_get_job", job_id=job_uuid)
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

        st, data = await state.call(
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

    st, data = await state.call(client, "hmc_list_recent_jobs", limit=20)
    state.record(12, "hmc_list_recent_jobs (post-tests)", st, data)
    # Opportunistically capture a job UUID if we still don't have one
    if not artifacts.job_uuid_sample and st == "PASS":
        for e in entries(data):
            if isinstance(e, dict) and e.get("type") != "error":
                artifacts.job_uuid_sample = e.get("UUID") or e.get("uuid")
                break


# ---------------------------------------------------------------------------
# ST5 — Metrics & Templates
# ---------------------------------------------------------------------------


async def inspect_metrics_templates(client: Client, state: RunState) -> None:
    config = state.config
    artifacts = state.artifacts
    print("\n=== ST5: Metrics & Templates ===")

    st, data = await state.call(
        client,
        "hmc_get_pcm_preferences",
        category="ManagedSystem",
        expected=[_PCM_UNLICENSED],
        resource_name_or_uuid=config.system_name,
    )
    state.record_with_expected(
        5,
        "hmc_get_pcm_preferences",
        st,
        data,
        [_PCM_UNLICENSED],
    )
    if st == "PASS":
        artifacts.lp3_baseline["pcm_prefs"] = data

    st, data = await state.call(
        client,
        "hmc_processed_metric_links",
        expected=[_PROCESSED_UNLICENSED],
        category="ManagedSystem",
        resource_name_or_uuid=config.system_name,
        start_ts="2026-01-01T00:00:00.000Z",
    )
    state.record_with_expected(
        5,
        "hmc_processed_metrics (links)",
        st,
        data,
        [_PROCESSED_UNLICENSED],
    )

    st, data = await state.call(
        client,
        "hmc_aggregated_metric_links",
        expected=[_AGGREGATED_UNLICENSED],
        category="ManagedSystem",
        resource_name_or_uuid=config.system_name,
        start_ts="2026-01-01T00:00:00.000Z",
    )
    state.record_with_expected(
        5,
        "hmc_aggregated_metrics (links)",
        st,
        data,
        [_AGGREGATED_UNLICENSED],
    )

    st, data = await state.call(
        client, "hmc_list_partition_templates", expected=[_TEMPLATES_UNLICENSED]
    )
    state.record_with_expected(
        5,
        "hmc_list_partition_templates",
        st,
        data,
        [_TEMPLATES_UNLICENSED],
    )
