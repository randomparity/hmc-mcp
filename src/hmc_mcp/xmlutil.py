"""Parsing helpers for the HMC uom (Atom/XML) payloads.

The HMC REST API returns Atom feeds where each <entry> wraps a single uom
resource in its own namespace, e.g.::

    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>urn:uuid:...</id>
        <title>ManagedSystem:7042-CR8*212345A</title>
        <link rel="SELF" href="https://hmc:12443/rest/api/uom/ManagedSystem/<uuid>"/>
        <content type="application/vnd.ibm.powervm.uom+xml">
          <ManagedSystem xmlns="http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/" ...>
            <SystemName>server1</SystemName>
            ...
          </ManagedSystem>
        </content>
      </entry>
    </feed>

We parse with defusedxml and flatten each entry into a plain dict whose keys
are the (namespace-stripped) element names. Lists are produced for repeated
element names.
"""

from __future__ import annotations

from typing import Any

# Element is used only as a type annotation; all XML parsing uses defusedxml.
from xml.etree.ElementTree import Element  # nosec B405

from defusedxml import ElementTree as DET

ATOM_NS = "http://www.w3.org/2005/Atom"

# web/mc namespace: Logon, HmcUser, HmcPasswordPolicy, HmcLdapServer docs.
WEB_NS = "http://www.ibm.com/xmlns/systems/power/firmware/web/mc/2012_10/"

# HMC bookkeeping attributes carried on nearly every uom element; they are
# noise for consumers, so we drop them during flattening.
_IGNORED_ATTRS = {"kb", "kxe", "kbo", "kb-cur", "schemaVersion", "lsb"}


def localname(tag: str) -> str:
    """Strip an XML namespace from a tag: '{ns}Name' -> 'Name'."""
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def element_to_dict(el: Element) -> dict[str, Any] | str:
    """Recursively convert an ElementTree element to plain Python data.

    - Leaf elements become their text (or their attribute dict if they only
      carry attributes, e.g. HMC's kb/kxe/kb-cur metadata attributes).
    - Repeated child element names are collected into a list.
    - Attributes are preserved under an "@attrs" key when an element also has
      children or meaningful text.

    Returns a dict for elements with children or attributes, otherwise the
    element's text as a string.
    """
    children = list(el)
    attrs = {
        localname(k): v for k, v in el.attrib.items() if localname(k) not in _IGNORED_ATTRS
    }
    text = (el.text or "").strip()

    if not children:
        if attrs and not text:
            return attrs
        if attrs:
            return {"@attrs": attrs, "text": text}
        return text

    result: dict[str, Any] = {}
    if attrs:
        result["@attrs"] = attrs

    for child in children:
        key = localname(child.tag)
        value = element_to_dict(child)
        if key in result:
            existing = result[key]
            if not isinstance(existing, list):
                result[key] = [existing]
            result[key].append(value)
        else:
            result[key] = value

    return result


def _parse_entry(entry: Element) -> dict[str, Any]:
    """Flatten one Atom entry and its wrapped HMC resource."""
    result: dict[str, Any] = {
        "UUID": None,
        "title": None,
        "link": None,
        "ResourceType": None,
        "Resource": {},
    }
    for child in entry:
        name = localname(child.tag)
        if name == "id":
            result["UUID"] = (child.text or "").strip().removeprefix("urn:uuid:")
        elif name == "title":
            result["title"] = (child.text or "").strip()
        elif name == "link" and child.attrib.get("rel", "SELF").upper() == "SELF":
            result["link"] = child.attrib.get("href")
        elif name == "content":
            resource = next(iter(child), None)
            if resource is not None:
                result["ResourceType"] = localname(resource.tag)
                result["Resource"] = element_to_dict(resource)
    return result


def parse_feed(xml_text: str) -> list[dict[str, Any]]:
    """Parse an HMC Atom feed into flattened resource dictionaries."""
    root = DET.fromstring(xml_text.encode("utf-8"))
    root_type = localname(root.tag)
    if root_type == "feed":
        return [_parse_entry(entry) for entry in root if localname(entry.tag) == "entry"]
    if root_type == "entry":
        return [_parse_entry(root)]
    return [
        {
            "UUID": None,
            "title": None,
            "link": None,
            "ResourceType": root_type,
            "Resource": element_to_dict(root),
        }
    ]


def find_text(xml_text: str, *names: str) -> str | None:
    """Return the text of the first element whose local name is in `names`."""
    root = DET.fromstring(xml_text.encode("utf-8"))
    wanted = set(names)
    for el in root.iter():
        if localname(el.tag) in wanted:
            return (el.text or "").strip()
    return None
