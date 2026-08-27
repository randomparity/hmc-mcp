"""MCP tools for Performance and Capacity Monitoring (PCM)."""

from __future__ import annotations

from ..tool_registry import tool_module

from typing import Any

from .._app import (
    run_sync,
)

from ..client.client_factory import client_from_env
from ..operations.pcm import (
    MetricKind,
    PcmCategory,
    get_pcm_preferences,
    metric_data,
    metric_links,
    preference_flags,
    set_pcm_preferences,
    validate_pcm_metric_target,
    validate_pcm_preferences_category,
)


tool, register_tools, tool_security = tool_module()


@tool(effect="read", operation="pcm.get_preferences", target_kind="metric_resource")
def hmc_get_pcm_preferences(
    category: PcmCategory, resource_name_or_uuid: str, profile: str | None = None
) -> dict[str, Any]:
    """Get managed-system PCM monitoring preferences.

    PCM preferences are documented only for ``ManagedSystem``.
    Returns flags like LongTermMonitorEnabled, AggregationEnabled,
    ShortTermMonitorEnabled, ComputeLTMEnabled, EnergyMonitorEnabled.

    Args:
        category: Must be ``ManagedSystem``.
        resource_name_or_uuid: Name or UUID of the selected system.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    validate_pcm_preferences_category(category)

    async def _go():
        async with client_from_env(profile) as hmc:
            return await get_pcm_preferences(hmc, category, resource_name_or_uuid)

    return run_sync(_go)


@tool(effect="mutate", operation="pcm.set_preferences", target_kind="metric_resource")
def hmc_set_pcm_preferences(
    category: PcmCategory,
    resource_name_or_uuid: str,
    long_term_monitor: bool | None = None,
    aggregation: bool | None = None,
    short_term_monitor: bool | None = None,
    compute_ltm: bool | None = None,
    energy_monitor: bool | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Enable/disable managed-system PCM data collection.

    PCM preferences are documented only for ``ManagedSystem``. Only the flags
    you set are changed. Turning on aggregation implicitly enables long-term
    monitoring on the HMC. Long-term + aggregation are required before
    processed/aggregated metrics become available. Returns the updated
    preferences dict (``{}`` if the HMC returns no body).

    Args:
        category: Must be ``ManagedSystem``.
        resource_name_or_uuid: Name or UUID of the selected system.
        long_term_monitor: Enable or disable long-term metric collection.
        aggregation: Enable or disable long-term metric aggregation.
        short_term_monitor: Enable or disable short-term metric collection.
        compute_ltm: Enable or disable computed long-term metrics.
        energy_monitor: Enable or disable energy metric collection.
        profile: TOML profile name, or the environment-default HMC when omitted.

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
    validate_pcm_preferences_category(category)

    async def _go():
        async with client_from_env(profile) as hmc:
            return await set_pcm_preferences(
                hmc, category, resource_name_or_uuid, flags
            )

    return run_sync(_go)


@tool(effect="read", operation="metrics.processed", target_kind="metric_resource")
def hmc_processed_metrics(
    category: PcmCategory,
    resource_name_or_uuid: str,
    start_ts: str,
    end_ts: str | None = None,
    no_of_samples: int | None = None,
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
) -> dict[str, Any]:
    """Download the newest processed PCM metrics JSON document.

    category is the resource type, e.g. 'ManagedSystem' or 'LogicalPartition';
    resource_name_or_uuid is the name or UUID of that resource. Processed
    metrics have 30s granularity and ~2h retention. Timestamps are ISO-8601
    UTC (yyyy-MM-ddTHH:mm:ssZ); start_ts is required.

    Returns the parsed JSON object of the most recent document, or ``{}`` when
    no metrics are available in the requested range. Use
    hmc_processed_metric_links to inspect the Atom feed.

    Args:
        category: ``ManagedSystem`` or ``LogicalPartition`` resource type.
        resource_name_or_uuid: Name or UUID of the selected system or partition.
        start_ts: Inclusive UTC start in ``yyyy-MM-ddTHH:mm:ssZ`` form.
        end_ts: Optional inclusive UTC end in the same format.
        no_of_samples: Optional maximum number of metric documents to request.
        system_name_or_uuid: Owning system for a ``LogicalPartition`` resource.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    return _metrics_fetch(
        category,
        resource_name_or_uuid,
        "processed",
        start_ts,
        end_ts,
        no_of_samples,
        profile=profile,
        system_name_or_uuid=system_name_or_uuid,
    )


@tool(effect="read", operation="metrics.processed_links", target_kind="metric_resource")
def hmc_processed_metric_links(
    category: PcmCategory,
    resource_name_or_uuid: str,
    start_ts: str,
    end_ts: str | None = None,
    no_of_samples: int | None = None,
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
) -> list[dict[str, str]]:
    """List processed PCM metric documents available in the requested range.

    Args:
        category: ``ManagedSystem`` or ``LogicalPartition`` resource type.
        resource_name_or_uuid: Name or UUID of the selected system or partition.
        start_ts: Inclusive UTC start in ``yyyy-MM-ddTHH:mm:ssZ`` form.
        end_ts: Optional inclusive UTC end in the same format.
        no_of_samples: Optional maximum number of feed entries to request.
        system_name_or_uuid: Owning system for a ``LogicalPartition`` resource.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    return _metrics_links(
        category,
        resource_name_or_uuid,
        "processed",
        start_ts,
        end_ts,
        no_of_samples,
        profile=profile,
        system_name_or_uuid=system_name_or_uuid,
    )


@tool(effect="read", operation="metrics.aggregated", target_kind="metric_resource")
def hmc_aggregated_metrics(
    category: PcmCategory,
    resource_name_or_uuid: str,
    start_ts: str,
    end_ts: str | None = None,
    no_of_samples: int | None = None,
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
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

    Args:
        category: ``ManagedSystem`` or ``LogicalPartition`` resource type.
        resource_name_or_uuid: Name or UUID of the selected system or partition.
        start_ts: Inclusive UTC start in ``yyyy-MM-ddTHH:mm:ssZ`` form.
        end_ts: Optional inclusive UTC end in the same format.
        no_of_samples: Optional maximum number of metric documents to request.
        system_name_or_uuid: Owning system for a ``LogicalPartition`` resource.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    return _metrics_fetch(
        category,
        resource_name_or_uuid,
        "aggregated",
        start_ts,
        end_ts,
        no_of_samples,
        profile=profile,
        system_name_or_uuid=system_name_or_uuid,
    )


@tool(
    effect="read", operation="metrics.aggregated_links", target_kind="metric_resource"
)
def hmc_aggregated_metric_links(
    category: PcmCategory,
    resource_name_or_uuid: str,
    start_ts: str,
    end_ts: str | None = None,
    no_of_samples: int | None = None,
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
) -> list[dict[str, str]]:
    """List aggregated PCM metric documents available in the requested range.

    Args:
        category: ``ManagedSystem`` or ``LogicalPartition`` resource type.
        resource_name_or_uuid: Name or UUID of the selected system or partition.
        start_ts: Inclusive UTC start in ``yyyy-MM-ddTHH:mm:ssZ`` form.
        end_ts: Optional inclusive UTC end in the same format.
        no_of_samples: Optional maximum number of feed entries to request.
        system_name_or_uuid: Owning system for a ``LogicalPartition`` resource.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    return _metrics_links(
        category,
        resource_name_or_uuid,
        "aggregated",
        start_ts,
        end_ts,
        no_of_samples,
        profile=profile,
        system_name_or_uuid=system_name_or_uuid,
    )


def _metrics_links(
    category: PcmCategory,
    resource_name_or_uuid: str,
    kind: MetricKind,
    start_ts: str,
    end_ts: str | None,
    no_of_samples: int | None,
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
) -> list[dict[str, str]]:
    validate_pcm_metric_target(category, system_name_or_uuid)

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
                system_name_or_uuid,
            )

    return run_sync(_go)


def _metrics_fetch(
    category: PcmCategory,
    resource_name_or_uuid: str,
    kind: MetricKind,
    start_ts: str,
    end_ts: str | None,
    no_of_samples: int | None,
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
) -> dict[str, Any]:
    validate_pcm_metric_target(category, system_name_or_uuid)

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
                system_name_or_uuid,
            )

    return run_sync(_go)
