"""Presentation-neutral PCM workflows shared by MCP and CLI adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ..client import HMCClient
from ..resource_identity import resolve_lpar_uuid, resolve_system_uuid
from ..errors import HMCError
from .error_translation import translate_pcm_error
from ..client.pcm_payloads import newest_metric_link

MetricKind = Literal["processed", "aggregated"]
PcmCategory = Literal["ManagedSystem", "LogicalPartition"]
PCM_CATEGORIES: frozenset[PcmCategory] = frozenset(
    {"ManagedSystem", "LogicalPartition"}
)


@dataclass(frozen=True)
class PcmResource:
    resource_uuid: str
    system_uuid: str | None = None


async def resolve_pcm_resource(
    hmc: HMCClient,
    category: PcmCategory,
    resource: str,
    system_name_or_uuid: str | None = None,
) -> PcmResource:
    if category == "ManagedSystem":
        if system_name_or_uuid is not None:
            raise ValueError(
                "system_name_or_uuid is valid only for LogicalPartition metrics."
            )
        return PcmResource(await resolve_system_uuid(hmc, resource))
    if category == "LogicalPartition":
        if system_name_or_uuid is None:
            raise ValueError(
                "LogicalPartition metrics require the owning system_name_or_uuid."
            )
        system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
        resource_uuid = await resolve_lpar_uuid(
            hmc, resource, system_name_or_uuid=system_uuid
        )
        return PcmResource(resource_uuid, system_uuid)
    return PcmResource(resource)


def validate_pcm_preferences_category(category: PcmCategory) -> None:
    if category != "ManagedSystem":
        raise ValueError(
            "PCM preferences are documented only for ManagedSystem; "
            "LogicalPartition is not supported."
        )


def validate_pcm_metric_target(
    category: PcmCategory, system_name_or_uuid: str | None
) -> None:
    if category == "LogicalPartition" and system_name_or_uuid is None:
        raise ValueError(
            "LogicalPartition metrics require the owning system_name_or_uuid."
        )
    if category == "ManagedSystem" and system_name_or_uuid is not None:
        raise ValueError(
            "system_name_or_uuid is valid only for LogicalPartition metrics."
        )


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
    validate_pcm_preferences_category(category)
    target = await resolve_pcm_resource(hmc, category, resource)
    try:
        return await hmc.get_pcm_preferences(category, target.resource_uuid)
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
    validate_pcm_preferences_category(category)
    target = await resolve_pcm_resource(hmc, category, resource)
    try:
        return await hmc.set_pcm_preferences(category, target.resource_uuid, **flags)
    except HMCError as exc:
        translate_pcm_error(exc)
        raise


async def metric_links(
    hmc: HMCClient,
    category: PcmCategory,
    resource: str,
    *,
    kind: MetricKind,
    start_ts: str,
    end_ts: str | None,
    no_of_samples: int | None,
    system_name_or_uuid: str | None = None,
) -> list[dict[str, str]]:
    validate_pcm_metric_target(category, system_name_or_uuid)
    target = await resolve_pcm_resource(
        hmc, category, resource, system_name_or_uuid=system_name_or_uuid
    )
    fetch = (
        hmc.get_processed_metric_links
        if kind == "processed"
        else hmc.get_aggregated_metric_links
    )
    try:
        return await fetch(
            category,
            target.resource_uuid,
            start_ts,
            end_ts,
            no_of_samples,
            system_uuid=target.system_uuid,
        )
    except HMCError as exc:
        translate_pcm_error(exc)
        raise


async def metric_data(
    hmc: HMCClient,
    category: PcmCategory,
    resource: str,
    *,
    kind: MetricKind,
    start_ts: str,
    end_ts: str | None,
    no_of_samples: int | None,
    system_name_or_uuid: str | None = None,
) -> dict[str, Any]:
    links = await metric_links(
        hmc,
        category,
        resource,
        kind=kind,
        start_ts=start_ts,
        end_ts=end_ts,
        no_of_samples=no_of_samples,
        system_name_or_uuid=system_name_or_uuid,
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
