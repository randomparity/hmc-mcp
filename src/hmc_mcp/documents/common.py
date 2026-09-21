"""Shared XML envelope infrastructure for HMC request documents."""

from __future__ import annotations

UOM_NS = "http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/"


def document_envelope(root_element: str, body: str, namespace: str = UOM_NS) -> str:
    """Wrap a document body in the standard HMC XML envelope."""
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<{root_element} xmlns="{namespace}" schemaVersion="V1_0">
{body}
</{root_element}>
'''
