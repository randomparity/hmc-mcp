"""MCP tools for Performance and Capacity Monitoring (PCM).
"""

from __future__ import annotations

from typing import Any, Literal

from ._app import (
    _READ_ONLY,
    _run,
    mcp,
    with_client,
)

from .client import HMCError
from .common import client_from_env
from .pcm import newest_metric_link



@mcp.tool(annotations=_READ_ONLY)
def hmc_get_pcm_preferences(category: str, resource_uuid: str) -> dict[str, Any]:
    """Get PCM monitoring preferences for a resource.

    category is the resource type, e.g. 'ManagedSystem' or 'LogicalPartition';
    resource_uuid is the UUID of that resource (from hmc_systems or
    hmc_lpars). Returns flags like LongTermMonitorEnabled,
    AggregationEnabled, ShortTermMonitorEnabled, ComputeLTMEnabled,
    EnergyMonitorEnabled.
    """

    return with_client(lambda hmc: hmc.get_pcm_preferences(category, resource_uuid))


@mcp.tool
def hmc_set_pcm_preferences(
    category: str,
    resource_uuid: str,
    long_term_monitor: bool | None = None,
    aggregation: bool | None = None,
    short_term_monitor: bool | None = None,
    compute_ltm: bool | None = None,
    energy_monitor: bool | None = None,
) -> dict[str, Any]:
    """Enable/disable PCM data collection for a resource.

    category is the resource type, e.g. 'ManagedSystem' or 'LogicalPartition';
    resource_uuid is the UUID of that resource. Only the flags you set are
    changed. Turning on aggregation implicitly enables long-term monitoring
    on the HMC. Long-term + aggregation are required before
    processed/aggregated metrics become available. Returns the updated
    preferences dict (``{}`` if the HMC returns no body).

    Raises:
        ValueError: if no preference flags are supplied.
    """
    flags: dict[str, bool] = {}
    if long_term_monitor is not None:
        flags["LongTermMonitorEnabled"] = long_term_monitor
    if aggregation is not None:
        flags["AggregationEnabled"] = aggregation
    if short_term_monitor is not None:
        flags["ShortTermMonitorEnabled"] = short_term_monitor
    if compute_ltm is not None:
        flags["ComputeLTMEnabled"] = compute_ltm
    if energy_monitor is not None:
        flags["EnergyMonitorEnabled"] = energy_monitor
    if not flags:
        raise ValueError("No preference flags supplied; nothing to change.")

    return with_client(lambda hmc: hmc.set_pcm_preferences(category, resource_uuid, **flags))


@mcp.tool(annotations=_READ_ONLY)
def hmc_processed_metrics(
    category: str,
    resource_uuid: str,
    start_ts: str,
    end_ts: str | None = None,
    no_of_samples: int | None = None,
    mode: Literal["links", "fetch"] = "links",
) -> list[dict[str, str]] | dict[str, Any]:
    """List or download processed PCM metrics.

    category is the resource type, e.g. 'ManagedSystem' or 'LogicalPartition';
    resource_uuid is the UUID of that resource. Processed metrics have 30s
    granularity and ~2h retention. Timestamps are ISO-8601 UTC
    (yyyy-MM-ddTHH:mm:ssZ); start_ts is required.

    mode selects the operation:
      "links" (default) — return the Atom feed links to the metric JSON
                          documents (list of dicts).
      "fetch"           — download and return the most recent metrics
                          document (dict); returns ``{}`` when no metrics are
                          available in the requested range.
    """
    if mode == "fetch":
        return _metrics_fetch(category, resource_uuid, "processed", start_ts, end_ts, no_of_samples)
    return _metrics_links(category, resource_uuid, "processed", start_ts, end_ts, no_of_samples)


@mcp.tool(annotations=_READ_ONLY)
def hmc_aggregated_metrics(
    category: str,
    resource_uuid: str,
    start_ts: str,
    end_ts: str | None = None,
    no_of_samples: int | None = None,
    mode: Literal["links", "fetch"] = "links",
) -> list[dict[str, str]] | dict[str, Any]:
    """List or download aggregated PCM metrics.

    category is the resource type, e.g. 'ManagedSystem' or 'LogicalPartition';
    resource_uuid is the UUID of that resource. Aggregated metrics are the
    long-term rollup used for trend analysis. Timestamps are ISO-8601 UTC
    (yyyy-MM-ddTHH:mm:ssZ); start_ts is required. Requires aggregation to
    be enabled in PCM preferences.

    mode selects the operation:
      "links" (default) — return the Atom feed links to the metric JSON
                          documents (list of dicts).
      "fetch"           — download and return the most recent metrics
                          document (dict); returns ``{}`` when no metrics are
                          available in the requested range.
    """
    if mode == "fetch":
        return _metrics_fetch(category, resource_uuid, "aggregated", start_ts, end_ts, no_of_samples)
    return _metrics_links(category, resource_uuid, "aggregated", start_ts, end_ts, no_of_samples)


async def _fetch_metric_links(
    hmc: Any,
    kind: Literal["processed", "aggregated"],
    category: str,
    resource_uuid: str,
    start_ts: str,
    end_ts: str | None,
    no_of_samples: int | None,
) -> list[dict[str, str]]:
    """Fetch the PCM metric feed via the client method for *kind*."""
    fn = (
        hmc.get_processed_metric_links
        if kind == "processed"
        else hmc.get_aggregated_metric_links
    )
    return await fn(category, resource_uuid, start_ts, end_ts, no_of_samples)


def _metrics_links(
    category: str,
    resource_uuid: str,
    kind: Literal["processed", "aggregated"],
    start_ts: str,
    end_ts: str | None,
    no_of_samples: int | None,
) -> list[dict[str, str]]:
    return with_client(
        lambda hmc: _fetch_metric_links(
            hmc, kind, category, resource_uuid, start_ts, end_ts, no_of_samples
        )
    )


def _metrics_fetch(
    category: str,
    resource_uuid: str,
    kind: Literal["processed", "aggregated"],
    start_ts: str,
    end_ts: str | None,
    no_of_samples: int | None,
) -> dict[str, Any]:
    async def _go():
        async with client_from_env() as hmc:
            links = await _fetch_metric_links(
                hmc, kind, category, resource_uuid, start_ts, end_ts, no_of_samples
            )
            if not links:
                return {}
            # Fetch the most recent metrics document. A 404 means the document
            # has aged out of PCM retention; surface that as no-data, matching
            # the tool contract (``{}`` when no metrics are available).
            try:
                return await hmc.fetch_json(newest_metric_link(links)["link"])
            except HMCError as exc:
                if exc.status_code == 404:
                    return {}
                raise

    return _run(_go)
