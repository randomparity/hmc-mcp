"""HMCClient pcm mixin.

The full client is assembled in :mod:`hmc_mcp.client` by inheriting every
domain mixin; this module only defines methods for pcm.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from .client_contracts import PcmClient
from .client_parse import _metric_links, _pcm_preferences
from ..errors import HMCError
from .pcm_payloads import build_pcm_preferences_document


class PcmMixin:
    # Performance and Capacity Monitoring (PCM)
    async def get_pcm_preferences(
        self: PcmClient, category: str, resource_uuid: str
    ) -> dict[str, Any]:
        """Get PCM preferences for a resource (e.g. ManagedSystem)."""
        _require_managed_system_preferences(category)

        path = f"/rest/api/pcm/{category}/{resource_uuid}/preferences"
        xml = await self._get(path)
        return _pcm_preferences(xml, path) if xml else {}

    async def set_pcm_preferences(
        self: PcmClient, category: str, resource_uuid: str, **flags: bool
    ) -> dict[str, Any]:
        """Set PCM preferences, e.g. LongTermMonitorEnabled=True.

        Only the flags you pass are changed; the HMC merges the rest. Returns
        the updated preferences document (``{}`` when the response body is
        empty).
        """
        _require_managed_system_preferences(category)

        xml = build_pcm_preferences_document(**flags)
        path = f"/rest/api/pcm/{category}/{resource_uuid}/preferences"
        resp_xml = await self._post_pcm(path, xml)
        return _pcm_preferences(resp_xml, path) if resp_xml else {}

    async def _post_pcm(self: PcmClient, path: str, body: str) -> str:
        resp = await self._request(
            "POST", path, content=body, headers={"Content-Type": "application/xml"}
        )
        if resp.status_code not in (200, 201, 202, 204):
            raise HMCError(f"POST {path} failed", resp.status_code, resp.text)
        return resp.text

    async def get_metrics_feed(self: PcmClient, path: str) -> list[dict[str, str]]:
        """GET a PCM metrics Atom feed and return its JSON links."""

        xml = await self._get(path)
        return _metric_links(xml, path) if xml else []

    async def get_processed_metric_links(
        self: PcmClient,
        category: str,
        resource_uuid: str,
        start_ts: str,
        end_ts: str | None = None,
        no_of_samples: int | None = None,
        *,
        system_uuid: str | None = None,
    ) -> list[dict[str, str]]:
        """Links to ProcessedMetrics JSON (30s granularity, ~2h retention)."""
        return await self._metrics_links(
            category,
            resource_uuid,
            "ProcessedMetrics",
            start_ts,
            end_ts,
            no_of_samples,
            system_uuid=system_uuid,
        )

    async def get_aggregated_metric_links(
        self: PcmClient,
        category: str,
        resource_uuid: str,
        start_ts: str,
        end_ts: str | None = None,
        no_of_samples: int | None = None,
        *,
        system_uuid: str | None = None,
    ) -> list[dict[str, str]]:
        """Links to AggregatedMetrics JSON (long-term rollup)."""
        return await self._metrics_links(
            category,
            resource_uuid,
            "AggregatedMetrics",
            start_ts,
            end_ts,
            no_of_samples,
            system_uuid=system_uuid,
        )

    async def get_ltm_metric_links(
        self: PcmClient,
        category: str,
        resource_uuid: str,
        start_ts: str,
        end_ts: str | None = None,
    ) -> list[dict[str, str]]:
        """Links to raw Long Term Monitor metrics JSON."""
        if category != "ManagedSystem":
            raise ValueError(
                "Long Term Monitor metrics are documented only for ManagedSystem; "
                "LogicalPartition is not supported."
            )
        return await self._metrics_links(
            category,
            resource_uuid,
            "RawMetrics/LongTermMonitor",
            start_ts,
            end_ts,
            None,
        )

    async def _metrics_links(
        self: PcmClient,
        category: str,
        resource_uuid: str,
        kind: str,
        start_ts: str,
        end_ts: str | None,
        no_of_samples: int | None,
        *,
        system_uuid: str | None = None,
    ) -> list[dict[str, str]]:
        if category == "LogicalPartition":
            if system_uuid is None:
                raise ValueError(
                    "LogicalPartition metrics require the owning system_name_or_uuid."
                )
            resource_path = (
                f"ManagedSystem/{system_uuid}/LogicalPartition/{resource_uuid}"
            )
        elif category == "ManagedSystem":
            if system_uuid is not None:
                raise ValueError(
                    "system_name_or_uuid is valid only for LogicalPartition metrics."
                )
            resource_path = f"ManagedSystem/{resource_uuid}"
        else:
            resource_path = f"{category}/{resource_uuid}"
        query: dict[str, str] = {"StartTS": start_ts}
        if end_ts:
            query["EndTS"] = end_ts
        if no_of_samples:
            query["NoOfSamples"] = str(no_of_samples)
        path = f"/rest/api/pcm/{resource_path}/{kind}?{urlencode(query)}"
        return await self.get_metrics_feed(path)

    async def fetch_json(self: PcmClient, link: str) -> dict[str, Any]:
        """Fetch a PCM metrics JSON document from an Atom link href.

        Raises HMCError for any non-200 response, including 404. A 404 means
        the metrics document has aged out of PCM retention (processed metrics
        keep ~2h, aggregated longer); callers that treat expiry as 'no data'
        catch HMCError and translate it (see hmc_processed_metrics).
        """
        url = link if link.startswith("http") else f"{self._rest_base_url}{link}"
        resp = await self._request("GET", url, headers={"Accept": "application/json"})
        if resp.status_code != 200:
            raise HMCError(f"GET {url} failed", resp.status_code, resp.text[:500])
        try:
            document = resp.json()
        except ValueError as exc:
            raise HMCError(
                f"GET {url} returned invalid JSON: {str(exc)[:500]}"
            ) from exc
        if not isinstance(document, dict):
            raise HMCError(
                f"GET {url} returned a JSON {type(document).__name__}; expected an object"
            )
        return document


def _require_managed_system_preferences(category: str) -> None:
    if category != "ManagedSystem":
        raise ValueError(
            "PCM preferences are documented only for ManagedSystem; "
            "LogicalPartition is not supported."
        )
