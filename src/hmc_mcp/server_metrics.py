"""MCP tools for Performance and Capacity Monitoring (PCM).
"""

from __future__ import annotations

from typing import Any, Literal

from ._app import (
    _READ_ONLY,
    _resolve_lpar_uuid,
    _resolve_system_uuid,
    _run,
    mcp,
)

from .client import HMCError
from .common import client_from_env
from .pcm import newest_metric_link


def _check_pcm_error(exc: HMCError) -> None:
    """Re-raise *exc* with an actionable message for known PCM HTTP errors.

    HTTP 406 means PCM is not licensed or not enabled on this HMC.
    HTTP 403 means the connecting user does not have PCM authority.
    All other errors are left unchanged.

    The replacement HMCError intentionally does not forward ``body=exc.body``:
    the constructor would append the parsed HMC body text after the actionable
    message, degrading readability.  ``from exc`` sets ``__cause__`` (rendered
    as "direct cause" in tracebacks) and, combined with the implicit
    ``__context__`` set by the ``except`` block, makes the original exception
    accessible in developer diagnostics.

    Note: the CLI ``metrics`` commands call ``PcmMixin`` methods directly and
    do not share this wrapper; they surface raw HTTP errors for 403/406.
    Narrowing that gap would require either pushing the translation into
    ``PcmMixin`` itself (client-layer change) or adding wrapping to
    ``cli_metrics.py`` — both are out of scope for issue #98.
    """
    if exc.status_code == 406:
        raise HMCError(
            "PCM is not licensed or not enabled on this HMC. "
            "Enable PCM in the HMC settings or use an HMC that has the PCM feature licensed.",
            exc.status_code,
        ) from exc
    if exc.status_code == 403:
        raise HMCError(
            "The connecting user does not have PCM authority on this HMC. "
            "Grant the user PCM authority in HMC user management and retry.",
            exc.status_code,
        ) from exc


async def _resolve_resource_uuid(hmc: Any, category: str, resource_name_or_uuid: str) -> str:
    """Resolve a PCM resource name-or-UUID based on its category.

    For 'ManagedSystem' uses _resolve_system_uuid; for 'LogicalPartition'
    uses _resolve_lpar_uuid. Other categories pass through untouched (only
    UUIDs are valid for other PCM resource types).
    """
    if category == "ManagedSystem":
        return await _resolve_system_uuid(hmc, resource_name_or_uuid)
    if category == "LogicalPartition":
        return await _resolve_lpar_uuid(hmc, resource_name_or_uuid)
    return resource_name_or_uuid


@mcp.tool(annotations=_READ_ONLY)
def hmc_get_pcm_preferences(category: str, resource_name_or_uuid: str) -> dict[str, Any]:
    """Get PCM monitoring preferences for a resource.

    category is the resource type, e.g. 'ManagedSystem' or 'LogicalPartition';
    resource_name_or_uuid is the name or UUID of that resource (a SystemName
    or UUID from hmc_systems, or a PartitionName or UUID from hmc_lpars).
    Returns flags like LongTermMonitorEnabled, AggregationEnabled,
    ShortTermMonitorEnabled, ComputeLTMEnabled, EnergyMonitorEnabled.
    """

    async def _go():
        async with client_from_env() as hmc:
            resource_uuid = await _resolve_resource_uuid(hmc, category, resource_name_or_uuid)
            try:
                return await hmc.get_pcm_preferences(category, resource_uuid)
            except HMCError as exc:
                _check_pcm_error(exc)
                raise

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

    async def _go():
        async with client_from_env() as hmc:
            resource_uuid = await _resolve_resource_uuid(hmc, category, resource_name_or_uuid)
            try:
                return await hmc.set_pcm_preferences(category, resource_uuid, **flags)
            except HMCError as exc:
                _check_pcm_error(exc)
                raise

    return _run(_go)


@mcp.tool(annotations=_READ_ONLY)
def hmc_processed_metrics(
    category: str,
    resource_name_or_uuid: str,
    start_ts: str,
    end_ts: str | None = None,
    no_of_samples: int | None = None,
    mode: Literal["links", "fetch"] = "fetch",
) -> list[dict[str, str]] | dict[str, Any]:
    """List or download processed PCM metrics JSON documents.

    category is the resource type, e.g. 'ManagedSystem' or 'LogicalPartition';
    resource_name_or_uuid is the name or UUID of that resource. Processed
    metrics have 30s granularity and ~2h retention. Timestamps are ISO-8601
    UTC (yyyy-MM-ddTHH:mm:ssZ); start_ts is required.

    mode='links' returns the Atom feed link list (list of dicts with 'link',
    'updated', 'title' keys). mode='fetch' (default) downloads and returns the
    parsed JSON of the most recent document, or ``{}`` when no metrics are
    available in the requested range.
    """
    if mode == "links":
        return _metrics_links(category, resource_name_or_uuid, "processed", start_ts, end_ts, no_of_samples)
    elif mode == "fetch":
        return _metrics_fetch(category, resource_name_or_uuid, "processed", start_ts, end_ts, no_of_samples)
    else:
        raise ValueError(f"Unknown mode {mode!r}. Expected 'links' or 'fetch'.")


@mcp.tool(annotations=_READ_ONLY)
def hmc_aggregated_metrics(
    category: str,
    resource_name_or_uuid: str,
    start_ts: str,
    end_ts: str | None = None,
    no_of_samples: int | None = None,
    mode: Literal["links", "fetch"] = "fetch",
) -> list[dict[str, str]] | dict[str, Any]:
    """List or download aggregated PCM metrics JSON documents.

    category is the resource type, e.g. 'ManagedSystem' or 'LogicalPartition';
    resource_name_or_uuid is the name or UUID of that resource. Aggregated
    metrics are the long-term rollup used for trend analysis. Timestamps are
    ISO-8601 UTC (yyyy-MM-ddTHH:mm:ssZ); start_ts is required.

    mode='links' returns the Atom feed link list. mode='fetch' (default)
    downloads and returns the parsed JSON of the most recent document, or
    ``{}`` when no metrics are available in the requested range. Requires
    aggregation to be enabled in PCM preferences.
    """
    if mode == "links":
        return _metrics_links(category, resource_name_or_uuid, "aggregated", start_ts, end_ts, no_of_samples)
    elif mode == "fetch":
        return _metrics_fetch(category, resource_name_or_uuid, "aggregated", start_ts, end_ts, no_of_samples)
    else:
        raise ValueError(f"Unknown mode {mode!r}. Expected 'links' or 'fetch'.")


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
    resource_name_or_uuid: str,
    kind: Literal["processed", "aggregated"],
    start_ts: str,
    end_ts: str | None,
    no_of_samples: int | None,
) -> list[dict[str, str]]:
    async def _go():
        async with client_from_env() as hmc:
            resource_uuid = await _resolve_resource_uuid(hmc, category, resource_name_or_uuid)
            try:
                return await _fetch_metric_links(
                    hmc, kind, category, resource_uuid, start_ts, end_ts, no_of_samples
                )
            except HMCError as exc:
                _check_pcm_error(exc)
                raise

    return _run(_go)


def _metrics_fetch(
    category: str,
    resource_name_or_uuid: str,
    kind: Literal["processed", "aggregated"],
    start_ts: str,
    end_ts: str | None,
    no_of_samples: int | None,
) -> dict[str, Any]:
    async def _go():
        async with client_from_env() as hmc:
            resource_uuid = await _resolve_resource_uuid(hmc, category, resource_name_or_uuid)
            try:
                links = await _fetch_metric_links(
                    hmc, kind, category, resource_uuid, start_ts, end_ts, no_of_samples
                )
            except HMCError as exc:
                _check_pcm_error(exc)
                raise
            if not links:
                return {}
            # Fetch the most recent metrics document. A 404 means the document
            # has aged out of PCM retention; surface that as no-data, matching
            # the tool contract (``{}`` when no metrics are available).
            # Note: 403/406 from name resolution (_resolve_resource_uuid) are
            # intentionally not wrapped: those endpoints are not PCM-specific,
            # so a PCM authority / not-licensed message would be misleading.
            try:
                return await hmc.fetch_json(newest_metric_link(links)["link"])
            except HMCError as exc:
                if exc.status_code == 404:
                    return {}
                _check_pcm_error(exc)
                raise

    return _run(_go)
