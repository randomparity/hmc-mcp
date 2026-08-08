"""Performance and Capacity Monitoring (PCM) helpers.

PCM is separate from the uom tree and returns *JSON* metric payloads, reached
via an Atom feed of links. Two request shapes:

  preferences:  GET/POST /rest/api/pcm/{Category}/{uuid}/preferences   (XML)
  metrics:      GET     /rest/api/pcm/{Category}/{uuid}/{Kind}[...]    (Atom feed
                of links) -> follow each link to a JSON document.

Categories: ManagementConsole, ManagedSystem, LogicalPartition,
VirtualIOServer, SharedStoragePool, Cluster.
Metric kinds: RawMetrics/LongTermMonitor, RawMetrics/ShortTermMonitor,
ProcessedMetrics, AggregatedMetrics.
"""

from __future__ import annotations

from typing import Any

from defusedxml import ElementTree as ET

PCM_NS = "http://www.ibm.com/xmlns/systems/power/firmware/pcm/mc/2012_10/"
ATOM = "{http://www.w3.org/2005/Atom}"

PREFERENCE_FIELDS = (
    "LongTermMonitorEnabled",
    "ShortTermMonitorEnabled",
    "AggregationEnabled",
    "ComputeLTMEnabled",
    "EnergyMonitorEnabled",
)


def build_pcm_preferences_document(**flags: bool) -> str:
    """Build a PCM preferences XML document.

    Only the flags you pass are included; omitted flags are left unchanged on
    the HMC (it merges). Flags use the exact HMC field names, e.g.
    LongTermMonitorEnabled=True, AggregationEnabled=True.
    """
    lines = []
    for name in PREFERENCE_FIELDS:
        if name in flags:
            val = "true" if flags[name] else "false"
            lines.append(f'  <{name} kb="CUD" kxe="false">{val}</{name}>')
    body = "\n".join(lines)
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<ManagementConsolePcmPreference xmlns="{PCM_NS}" schemaVersion="V1_1_0">
  <Metadata><Atom/></Metadata>
{body}
</ManagementConsolePcmPreference>
"""


def pcm_preferences_to_dict(xml: str) -> dict[str, Any]:
    """Parse a PCM preferences GET response into {field: bool/str}.

    Raises:
        ET.ParseError: If *xml* is malformed — callers must not mistake a
            parse failure for "no preferences" (an empty dict is returned
            only for well-formed XML without recognized preference fields).
    """
    root = ET.fromstring(xml)
    out: dict[str, Any] = {}
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in PREFERENCE_FIELDS or tag in ("EnergyMonitoringCapable",):
            text = (el.text or "").strip()
            out[tag] = text.lower() in ("true", "1") if text else text
    return out


def metric_links(feed_xml: str) -> list[dict[str, str]]:
    """Extract metric JSON links from a PCM Atom feed.

    Returns a list of {link, updated, title} — 'link' is the absolute or
    relative URL to the JSON metrics document. An empty list means a
    well-formed feed with no entries (e.g. no metrics in the requested
    range), never a parse failure.

    Raises:
        ET.ParseError: If *feed_xml* is malformed — propagated so callers
            surface the failure instead of mistaking it for "no metrics".
    """
    root = ET.fromstring(feed_xml)
    links = []
    for entry in root.iter(f"{ATOM}entry"):
        link = entry.find(f"{ATOM}link")
        updated = entry.find(f"{ATOM}updated")
        title = entry.find(f"{ATOM}title")
        if link is not None and link.get("href"):
            links.append(
                {
                    "link": link.get("href") or "",
                    "updated": (updated.text or "").strip() if updated is not None else "",
                    "title": (title.text or "").strip() if title is not None else "",
                }
            )
    return links
