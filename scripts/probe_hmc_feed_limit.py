"""Probe HMC feed limiting mechanisms — issue #154.

Tests whether the HMC REST API honours any source-side limiting query parameter
across four feed shapes: root UOM, child UOM, search-filtered, and Job.

Candidates tested (in order, one session per candidate):
  - ?limit=N          (generic REST convention)
  - ?_limit=N         (some IBM REST APIs)
  - ?maxCount=N       (alternative IBM naming)
  - ?count=N          (Atom pagination convention)

A candidate is considered to have worked only when the actual Atom <entry>
count is strictly less than the unlimited baseline AND consistently equals the
requested limit across three repetitions. HTTP 200 alone is NOT sufficient.

Usage:
    uv run python scripts/probe_hmc_feed_limit.py > probe_results.json

Post-run: inspect probe_results.json for any UUIDs or identifying strings
before sharing. The script writes only entry counts, byte counts, SHA-256
fingerprints, and HTTP metadata — no credentials, tokens, or raw XML.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import warnings
from dataclasses import dataclass
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

import httpx

from hmc_mcp.config import load_profile
from hmc_mcp.client import HMCClient, MEDIA_UOM

ATOM_NS = "{http://www.w3.org/2005/Atom}"
REPETITIONS = 3
LIMIT_VALUES = (1, 2)

# Query-parameter candidates to probe; each is tried independently.
CANDIDATES = [
    "limit",
    "_limit",
    "maxCount",
    "count",
]


@dataclass(frozen=True)
class FeedCase:
    name: str
    resource_type: str
    path_template: str  # may contain {lpar_uuid}


def uom_headers(resource_type: str) -> dict[str, str]:
    return {
        "Accept": f"{MEDIA_UOM}; type={resource_type}Feed",
    }


def with_query_param(path: str, key: str, value: int) -> str:
    parts = urlsplit(path)
    existing = parts.query
    added = urlencode({key: value})
    query = "&".join(p for p in (existing, added) if p)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def summarize_xml(body: bytes) -> dict[str, object]:
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        return {"parse_error": str(exc)}
    entries = root.findall(f"{ATOM_NS}entry")
    fingerprints = [
        hashlib.sha256(ElementTree.tostring(e)).hexdigest()[:16] for e in entries
    ]
    next_links = [
        link.attrib.get("href", "")[: 80]
        for link in root.findall(f".//{ATOM_NS}link")
        if link.attrib.get("rel") == "next"
    ]
    total_count = None
    # Some Atom feeds expose opensearch:totalResults or similar
    for tag_candidate in [
        "{http://a9.com/-/spec/opensearch/1.1/}totalResults",
        "{http://a9.com/-/spec/opensearch/1.0/}totalResults",
    ]:
        el = root.find(f".//{tag_candidate}")
        if el is not None and el.text:
            try:
                total_count = int(el.text.strip())
            except ValueError:
                pass
            break
    return {
        "entries": len(entries),
        "entry_fingerprints": fingerprints,
        "next_links": next_links,
        "total_count_metadata": total_count,
    }


async def probe_one_candidate(
    http: httpx.AsyncClient,
    cases: list[FeedCase],
    param_name: str,
) -> dict[str, object]:
    """Run all cases for a single query-parameter candidate."""
    candidate_results: list[dict[str, object]] = []

    for case in cases:
        path = case.path_template

        # Unlimited baseline (3 reps to confirm stability)
        baseline_entries: int | None = None
        for rep in range(1, REPETITIONS + 1):
            resp = await http.get(path, headers=uom_headers(case.resource_type))
            row: dict[str, object] = {
                "case": case.name,
                "param": None,
                "requested": None,
                "repetition": rep,
                "status": resp.status_code,
                "bytes": len(resp.content),
                "content_type": resp.headers.get("content-type", ""),
            }
            if resp.status_code == 200 and resp.content:
                row.update(summarize_xml(resp.content))
                if baseline_entries is None:
                    baseline_entries = row.get("entries")  # type: ignore[assignment]
            candidate_results.append(row)

        if baseline_entries is None or baseline_entries < 3:
            # Record skip — not enough entries to prove limiting
            for n in LIMIT_VALUES:
                candidate_results.append(
                    {
                        "case": case.name,
                        "param": param_name,
                        "requested": n,
                        "repetition": "skipped",
                        "reason": f"baseline only {baseline_entries} entries — cannot prove limiting",
                    }
                )
            continue

        # Limited probes
        for n in LIMIT_VALUES:
            limited_path = with_query_param(path, param_name, n)
            for rep in range(1, REPETITIONS + 1):
                resp = await http.get(
                    limited_path, headers=uom_headers(case.resource_type)
                )
                row = {
                    "case": case.name,
                    "param": param_name,
                    "requested": n,
                    "repetition": rep,
                    "status": resp.status_code,
                    "bytes": len(resp.content),
                    "content_type": resp.headers.get("content-type", ""),
                }
                if resp.status_code == 200 and resp.content:
                    row.update(summarize_xml(resp.content))
                candidate_results.append(row)

    return {"candidate": param_name, "rows": candidate_results}


async def gather_lpar_uuids(hmc: HMCClient) -> list[str]:
    """Return UUIDs for up to 3 LPARs from the root feed."""
    xml = await hmc._get("/rest/api/uom/LogicalPartition", "LogicalPartition")
    if not xml:
        return []
    root = ElementTree.fromstring(xml.encode() if isinstance(xml, str) else xml)
    uuids: list[str] = []
    for entry in root.findall(f"{ATOM_NS}entry"):
        uuid_el = entry.find(f"{ATOM_NS}id")
        if uuid_el is not None and uuid_el.text:
            # HMC id element typically ends with the UUID
            val = uuid_el.text.strip().split(":")[-1]
            if len(val) == 36 and val.count("-") == 4:
                uuids.append(val)
        if len(uuids) >= 3:
            break
    return uuids


async def run() -> None:
    warnings.filterwarnings("ignore")
    config = load_profile()
    print(f"# Profile: host={config.host} user={config.user}", flush=True)

    async with HMCClient(config) as hmc:
        # Discover LPAR UUIDs so we can build child and search paths
        lpar_uuids = await gather_lpar_uuids(hmc)
        print(f"# Found {len(lpar_uuids)} LPAR UUIDs", flush=True)

        if len(lpar_uuids) < 3:
            print("# WARNING: fewer than 3 LPARs — some cases may be skipped")

        # Build cases using actual UUIDs; redact them in output
        cases: list[FeedCase] = [
            FeedCase(
                name="root-LogicalPartition",
                resource_type="LogicalPartition",
                path_template="/rest/api/uom/LogicalPartition",
            ),
        ]

        if lpar_uuids:
            # Child feed under the first LPAR
            cases.append(
                FeedCase(
                    name="child-ClientNetworkAdapter",
                    resource_type="ClientNetworkAdapter",
                    path_template=f"/rest/api/uom/LogicalPartition/{lpar_uuids[0]}/ClientNetworkAdapter",
                )
            )
            # Search-filtered feed
            cases.append(
                FeedCase(
                    name="search-LogicalPartition-by-state",
                    resource_type="LogicalPartition",
                    path_template="/rest/api/uom/LogicalPartition/search/(PartitionState==not activated)",
                )
            )

        # Job feed
        cases.append(
            FeedCase(
                name="root-Job",
                resource_type="Job",
                path_template="/rest/api/uom/Job",
            )
        )

        # Run each candidate in the same session
        all_results: list[dict[str, object]] = []
        for param_name in CANDIDATES:
            print(f"# Probing candidate: ?{param_name}=N", flush=True)
            result = await probe_one_candidate(
                hmc._http,  # reuse the already-logged-on httpx client
                cases,
                param_name,
            )
            all_results.append(result)

    # Redact UUIDs from output — replace any 36-char UUID-shaped string
    import re

    output_str = json.dumps(all_results, indent=2)
    output_str = re.sub(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        "<UUID-REDACTED>",
        output_str,
    )

    print(output_str)


if __name__ == "__main__":
    asyncio.run(run())
