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



@mcp.tool(annotations=_READ_ONLY)
def hmc_get_pcm_preferences(category: str, uuid: str) -> dict[str, Any]:
    """Get PCM monitoring preferences for a resource.

    category is e.g. 'ManagedSystem' or 'LogicalPartition'. Returns flags like
    LongTermMonitorEnabled, AggregationEnabled, ShortTermMonitorEnabled,
    ComputeLTMEnabled, EnergyMonitorEnabled.
    """

    return with_client(lambda hmc: hmc.get_pcm_preferences(category, uuid))


@mcp.tool
def hmc_set_pcm_preferences(
    category: str,
    uuid: str,
    long_term_monitor: bool | None = None,
    aggregation: bool | None = None,
    short_term_monitor: bool | None = None,
    compute_ltm: bool | None = None,
    energy_monitor: bool | None = None,
) -> str:
    """Enable/disable PCM data collection for a resource.

    Only the flags you set are changed. Turning on aggregation implicitly
    enables long-term monitoring on the HMC. Long-term + aggregation are
    required before processed/aggregated metrics become available.
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
        return "No preference flags supplied; nothing to change."

    with_client(lambda hmc: hmc.set_pcm_preferences(category, uuid, **flags))
    return f"Updated PCM preferences on {category} {uuid}: {flags}"


@mcp.tool(annotations=_READ_ONLY)
def hmc_get_processed_metric_links(
    category: str,
    uuid: str,
    start_ts: str,
    end_ts: str | None = None,
    no_of_samples: int | None = None,
) -> list[dict[str, str]]:
    """List available processed PCM metrics JSON documents.

    Processed metrics have 30s granularity and ~2h retention. Timestamps are
    ISO-8601 UTC (yyyy-MM-ddTHH:mm:ssZ); start_ts is required. Returns the
    Atom feed links to the metric JSON documents. Pass one link's ``link``
    value to hmc_fetch_json, or call hmc_get_processed_metrics to download
    the most recent document directly.
    """
    return _metrics_links(category, uuid, "processed", start_ts, end_ts, no_of_samples)


@mcp.tool(annotations=_READ_ONLY)
def hmc_get_processed_metrics(
    category: str,
    uuid: str,
    start_ts: str,
    end_ts: str | None = None,
    no_of_samples: int | None = None,
) -> dict[str, Any]:
    """Download the most recent processed PCM metrics JSON document.

    Same time-range arguments as hmc_get_processed_metric_links. Returns the
    parsed JSON of the newest document, or ``{}`` when no metrics are
    available in the requested range.
    """
    return _metrics_fetch(category, uuid, "processed", start_ts, end_ts, no_of_samples)


@mcp.tool(annotations=_READ_ONLY)
def hmc_get_aggregated_metric_links(
    category: str,
    uuid: str,
    start_ts: str,
    end_ts: str | None = None,
    no_of_samples: int | None = None,
) -> list[dict[str, str]]:
    """List available aggregated PCM metrics JSON documents.

    Aggregated metrics are the long-term rollup used for trend analysis.
    Timestamps are ISO-8601 UTC (yyyy-MM-ddTHH:mm:ssZ); start_ts is required.
    Returns the Atom feed links to the metric JSON documents. Pass one link's
    ``link`` value to hmc_fetch_json, or call hmc_get_aggregated_metrics to
    download the most recent document directly.
    """
    return _metrics_links(category, uuid, "aggregated", start_ts, end_ts, no_of_samples)


@mcp.tool(annotations=_READ_ONLY)
def hmc_get_aggregated_metrics(
    category: str,
    uuid: str,
    start_ts: str,
    end_ts: str | None = None,
    no_of_samples: int | None = None,
) -> dict[str, Any]:
    """Download the most recent aggregated PCM metrics JSON document.

    Same time-range arguments as hmc_get_aggregated_metric_links. Requires
    aggregation to be enabled in PCM preferences. Returns the parsed JSON of
    the newest document, or ``{}`` when no metrics are available in the
    requested range.
    """
    return _metrics_fetch(category, uuid, "aggregated", start_ts, end_ts, no_of_samples)


def _metric_links_method(hmc, kind: Literal["processed", "aggregated"]):
    """Select the client link-fetch method for a PCM metric kind."""
    return (
        hmc.get_processed_metric_links
        if kind == "processed"
        else hmc.get_aggregated_metric_links
    )


def _metrics_links(
    category: str,
    uuid: str,
    kind: Literal["processed", "aggregated"],
    start_ts: str,
    end_ts: str | None,
    no_of_samples: int | None,
) -> list[dict[str, str]]:
    return with_client(
        lambda hmc: _metric_links_method(hmc, kind)(
            category, uuid, start_ts, end_ts, no_of_samples
        )
    )


def _metrics_fetch(
    category: str,
    uuid: str,
    kind: Literal["processed", "aggregated"],
    start_ts: str,
    end_ts: str | None,
    no_of_samples: int | None,
) -> dict[str, Any]:
    async def _go():
        async with client_from_env() as hmc:
            links = await _metric_links_method(hmc, kind)(
                category, uuid, start_ts, end_ts, no_of_samples
            )
            if not links:
                return {}
            # Fetch the most recent metrics document. A 404 means the document
            # has aged out of PCM retention; surface that as no-data, matching
            # the tool contract (``{}`` when no metrics are available).
            try:
                return await hmc.fetch_json(links[-1]["link"])
            except HMCError as exc:
                if exc.status_code == 404:
                    return {}
                raise

    return _run(_go())

