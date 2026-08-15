"""Presentation-neutral PCM workflows shared by MCP and CLI adapters."""

from __future__ import annotations

from typing import Any, Literal

from .client import HMCClient
from .common import resolve_lpar_uuid, resolve_system_uuid
from .errors import HMCError
from .error_translation import translate_pcm_error
from .pcm import newest_metric_link

MetricKind = Literal["processed", "aggregated"]
PcmCategory = Literal["ManagedSystem", "LogicalPartition"]
PCM_CATEGORIES: frozenset[PcmCategory] = frozenset(
    {"ManagedSystem", "LogicalPartition"}
)


async def resolve_pcm_resource(
    hmc: HMCClient, category: PcmCategory, resource: str
) -> str:
    if category == "ManagedSystem":
        return await resolve_system_uuid(hmc, resource)
    if category == "LogicalPartition":
        return await resolve_lpar_uuid(hmc, resource)
    return resource


def preference_flags(
    long_term_monitor: bool | None = None,
    aggregation: bool | None = None,
    short_term_monitor: bool | None = None,
    compute_ltm: bool | None = None,
    energy_monitor: bool | None = None,
) -> dict[str, bool]:
    values = {
        "LongTermMonitorEnabled": long_term_monitor,
        "AggregationEnabled": aggregation,
        "ShortTermMonitorEnabled": short_term_monitor,
        "ComputeLTMEnabled": compute_ltm,
        "EnergyMonitorEnabled": energy_monitor,
    }
    return {name: value for name, value in values.items() if value is not None}


async def get_pcm_preferences(
    hmc: HMCClient, category: PcmCategory, resource: str
) -> dict[str, Any]:
    resource_uuid = await resolve_pcm_resource(hmc, category, resource)
    try:
        return await hmc.get_pcm_preferences(category, resource_uuid)
    except HMCError as exc:
        translate_pcm_error(exc)
        raise


async def set_pcm_preferences(
    hmc: HMCClient,
    category: PcmCategory,
    resource: str,
    flags: dict[str, bool],
) -> dict[str, Any]:
    if not flags:
        raise ValueError("No preference flags supplied; nothing to change.")
    resource_uuid = await resolve_pcm_resource(hmc, category, resource)
    try:
        return await hmc.set_pcm_preferences(category, resource_uuid, **flags)
    except HMCError as exc:
        translate_pcm_error(exc)
        raise


async def metric_links(
    hmc: HMCClient,
    category: PcmCategory,
    resource: str,
    kind: MetricKind,
    start_ts: str,
    end_ts: str | None,
    no_of_samples: int | None,
) -> list[dict[str, str]]:
    resource_uuid = await resolve_pcm_resource(hmc, category, resource)
    fetch = (
        hmc.get_processed_metric_links
        if kind == "processed"
        else hmc.get_aggregated_metric_links
    )
    try:
        return await fetch(category, resource_uuid, start_ts, end_ts, no_of_samples)
    except HMCError as exc:
        translate_pcm_error(exc)
        raise


async def metric_data(
    hmc: HMCClient,
    category: PcmCategory,
    resource: str,
    kind: MetricKind,
    start_ts: str,
    end_ts: str | None,
    no_of_samples: int | None,
) -> dict[str, Any]:
    links = await metric_links(
        hmc, category, resource, kind, start_ts, end_ts, no_of_samples
    )
    if not links:
        return {}
    try:
        return await hmc.fetch_json(newest_metric_link(links)["link"])
    except HMCError as exc:
        if exc.status_code == 404:
            return {}
        translate_pcm_error(exc)
        raise
