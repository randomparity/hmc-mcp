"""Shared XML envelope helpers used by domain-specific document builders."""

from __future__ import annotations

UOM_NS = "http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/"


def document_envelope(root_element: str, body: str, namespace: str = UOM_NS) -> str:
    """Wrap a document body in the standard HMC XML envelope."""
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<{root_element} xmlns="{namespace}" schemaVersion="V1_0">
{body}
</{root_element}>
"""


def lpar_envelope(body: str) -> str:
    """Wrap an LPAR document body in the LogicalPartition XML envelope."""
    return document_envelope("LogicalPartition", body)
