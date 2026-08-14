"""MCP tools for Performance and Capacity Monitoring (PCM)."""

from __future__ import annotations

from typing import Any

from ._app import (
    _READ_ONLY,
    _run,
    mcp,
)

from .common import client_from_env
from .operations_pcm import (
    MetricKind,
    get_pcm_preferences,
    metric_data,
    metric_links,
    preference_flags,
    set_pcm_preferences,
)


@mcp.tool(annotations=_READ_ONLY)
def hmc_get_pcm_preferences(
    category: str, resource_name_or_uuid: str, profile: str | None = None
) -> dict[str, Any]:
    """Get PCM monitoring preferences for a resource.

    category is the resource type, e.g. 'ManagedSystem' or 'LogicalPartition';
    resource_name_or_uuid is the name or UUID of that resource (a SystemName
    or UUID from hmc_list_systems, or a PartitionName or UUID from hmc_list_lpars).
    Returns flags like LongTermMonitorEnabled, AggregationEnabled,
    ShortTermMonitorEnabled, ComputeLTMEnabled, EnergyMonitorEnabled.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await get_pcm_preferences(hmc, category, resource_name_or_uuid)

    return _run(_go)


@mcp.tool
def hmc_set_pcm_preferences(
    category: str,
    resource_name_or_uuid: str,
    long_term_monitor: bool | None = None,
    aggregation: bool | None = None,
    short_term_monitor: bool | None = None,
    compute_ltm: bool | None = None,
    energy_monitor: bool | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Enable/disable PCM data collection for a resource.

    category is the resource type, e.g. 'ManagedSystem' or 'LogicalPartition';
    resource_name_or_uuid is the name or UUID of that resource. Only the flags
    you set are changed. Turning on aggregation implicitly enables long-term
    monitoring on the HMC. Long-term + aggregation are required before
    processed/aggregated metrics become available. Returns the updated
    preferences dict (``{}`` if the HMC returns no body).

    Raises:
        ValueError: if no preference flags are supplied.
    """
    flags = preference_flags(
        long_term_monitor,
        aggregation,
        short_term_monitor,
        compute_ltm,
        energy_monitor,
    )
    if not flags:
        raise ValueError("No preference flags supplied; nothing to change.")

    async def _go():
        async with client_from_env(profile) as hmc:
            return await set_pcm_preferences(
                hmc, category, resource_name_or_uuid, flags
            )

    return _run(_go)


@mcp.tool(annotations=_READ_ONLY)
def hmc_processed_metrics(
    category: str,
    resource_name_or_uuid: str,
    start_ts: str,
    end_ts: str | None = None,
    no_of_samples: int | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Download the newest processed PCM metrics JSON document.

    category is the resource type, e.g. 'ManagedSystem' or 'LogicalPartition';
    resource_name_or_uuid is the name or UUID of that resource. Processed
    metrics have 30s granularity and ~2h retention. Timestamps are ISO-8601
    UTC (yyyy-MM-ddTHH:mm:ssZ); start_ts is required.

    Returns the parsed JSON object of the most recent document, or ``{}`` when
    no metrics are available in the requested range. Use
    hmc_processed_metric_links to inspect the Atom feed.
    """
    return _metrics_fetch(
        category,
        resource_name_or_uuid,
        "processed",
        start_ts,
        end_ts,
        no_of_samples,
        profile,
    )


@mcp.tool(annotations=_READ_ONLY)
def hmc_processed_metric_links(
    category: str,
    resource_name_or_uuid: str,
    start_ts: str,
    end_ts: str | None = None,
    no_of_samples: int | None = None,
    profile: str | None = None,
) -> list[dict[str, str]]:
    """List processed PCM metric documents available in the requested range."""
    return _metrics_links(
        category,
        resource_name_or_uuid,
        "processed",
        start_ts,
        end_ts,
        no_of_samples,
        profile,
    )


@mcp.tool(annotations=_READ_ONLY)
def hmc_aggregated_metrics(
    category: str,
    resource_name_or_uuid: str,
    start_ts: str,
    end_ts: str | None = None,
    no_of_samples: int | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Download the newest aggregated PCM metrics JSON document.

    category is the resource type, e.g. 'ManagedSystem' or 'LogicalPartition';
    resource_name_or_uuid is the name or UUID of that resource. Aggregated
    metrics are the long-term rollup used for trend analysis. Timestamps are
    ISO-8601 UTC (yyyy-MM-ddTHH:mm:ssZ); start_ts is required.

    Returns the parsed JSON object of the most recent document, or ``{}`` when
    no metrics are available in the requested range. Requires aggregation to
    be enabled in PCM preferences. Use hmc_aggregated_metric_links to inspect
    the Atom feed.
    """
    return _metrics_fetch(
        category,
        resource_name_or_uuid,
        "aggregated",
        start_ts,
        end_ts,
        no_of_samples,
        profile,
    )


@mcp.tool(annotations=_READ_ONLY)
def hmc_aggregated_metric_links(
    category: str,
    resource_name_or_uuid: str,
    start_ts: str,
    end_ts: str | None = None,
    no_of_samples: int | None = None,
    profile: str | None = None,
) -> list[dict[str, str]]:
    """List aggregated PCM metric documents available in the requested range."""
    return _metrics_links(
        category,
        resource_name_or_uuid,
        "aggregated",
        start_ts,
        end_ts,
        no_of_samples,
        profile,
    )


def _metrics_links(
    category: str,
    resource_name_or_uuid: str,
    kind: MetricKind,
    start_ts: str,
    end_ts: str | None,
    no_of_samples: int | None,
    profile: str | None = None,
) -> list[dict[str, str]]:
    async def _go():
        async with client_from_env(profile) as hmc:
            return await metric_links(
                hmc,
                category,
                resource_name_or_uuid,
                kind,
                start_ts,
                end_ts,
                no_of_samples,
            )

    return _run(_go)


def _metrics_fetch(
    category: str,
    resource_name_or_uuid: str,
    kind: MetricKind,
    start_ts: str,
    end_ts: str | None,
    no_of_samples: int | None,
    profile: str | None = None,
) -> dict[str, Any]:
    async def _go():
        async with client_from_env(profile) as hmc:
            return await metric_data(
                hmc,
                category,
                resource_name_or_uuid,
                kind,
                start_ts,
                end_ts,
                no_of_samples,
            )

    return _run(_go)
