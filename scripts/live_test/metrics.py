"""Metrics and job-monitoring scenarios for the live HMC test harness."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp import Client


from .results import entries

if TYPE_CHECKING:
    from live_test_runner import RunState

# ---------------------------------------------------------------------------
# ST12 — PCM Metrics & Job Monitoring
# ---------------------------------------------------------------------------


async def inspect_metrics_jobs(client: Client, state: RunState) -> None:
    context = state.context
    print("\n=== ST12: PCM Metrics & Job Monitoring ===")

    st, data = await state.call(
        client,
        "hmc_get_pcm_preferences",
        category="ManagedSystem",
        resource_name_or_uuid=context.system_name,
    )
    state.record_expected_or_real(
        12,
        "hmc_get_pcm_preferences",
        st,
        data,
        expected_fail_substrings=["PCM", "406", "403"],
        skip_reason="PCM not licensed on this HMC (expected)",
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
            resource_name_or_uuid=context.system_name,
            long_term_monitor=new_ltm,
        )
        state.record(12, "hmc_set_pcm_preferences (toggle)", st, data)

        st, data = await state.call(
            client,
            "hmc_get_pcm_preferences",
            category="ManagedSystem",
            resource_name_or_uuid=context.system_name,
        )
        state.record(12, "hmc_get_pcm_preferences (verify)", st, data)

        st, data = await state.call(
            client,
            "hmc_set_pcm_preferences",
            category="ManagedSystem",
            resource_name_or_uuid=context.system_name,
            long_term_monitor=bool(current_ltm),
        )
        state.record(12, "hmc_set_pcm_preferences (restore)", st, data)
    else:
        state.skip(
            12,
            "hmc_set_pcm_preferences",
            "PCM not licensed/enabled on this HMC (expected)",
        )

    job_uuid = context.job_uuid_sample
    if job_uuid:
        st, data = await state.call(client, "hmc_get_job", job_uuid=job_uuid)
        state.record_expected_or_real(
            12,
            "hmc_get_job",
            st,
            data,
            expected_fail_substrings=["REST000E", "REST000B", "400"],
            skip_reason="Job REST type not supported on this HMC firmware",
        )

        st, data = await state.call(
            client,
            "hmc_wait_for_job",
            job_uuid=job_uuid,
            timeout_seconds=10,
            poll_interval=2,
        )
        state.record_expected_or_real(
            12,
            "hmc_wait_for_job",
            st,
            data,
            expected_fail_substrings=["REST000E", "REST000B", "400"],
            skip_reason="Job REST type not supported on this HMC firmware",
        )
    else:
        state.skip(12, "hmc_get_job", "no job UUID captured (ST8 may have failed)")
        state.skip(12, "hmc_wait_for_job", "no job UUID")

    st, data = await state.call(client, "hmc_list_recent_jobs", limit=20)
    state.record(12, "hmc_list_recent_jobs (post-tests)", st, data)
    # Opportunistically capture a job UUID if we still don't have one
    if not context.job_uuid_sample and st == "PASS":
        for e in entries(data):
            if isinstance(e, dict) and e.get("type") != "error":
                context.job_uuid_sample = e.get("UUID") or e.get("uuid")
                break

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
