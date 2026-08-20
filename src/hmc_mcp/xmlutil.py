"""Parsing and escaping helpers for the HMC uom (Atom/XML) payloads.

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

Outbound documents are rendered from string templates in ``documents.py`` and
``jobs.py``. ``escape_xml`` and ``escapes_string_arguments`` below are the
package's only encoding boundary for those templates; see ADR 0042.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, ParamSpec, cast

# Element is used only as a type annotation; all XML parsing uses defusedxml.
from xml.etree.ElementTree import Element  # nosec B405
from xml.sax.saxutils import escape  # nosec B406

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


# ====================================================================== #
# Outbound escaping (ADR 0042)
# ====================================================================== #

# The two metacharacters saxutils.escape does not handle by default. Escaping
# all five means one escaped form is safe as character data and inside a
# single- or double-quoted attribute value alike, so a builder never has to
# pick an encoding per interpolation site.
_ATTRIBUTE_ENTITIES = {'"': "&quot;", "'": "&apos;"}


class _EscapedXmlText(str):
    """A string already escaped for XML output.

    Carrying the fact in the type is what makes ``escape_xml`` idempotent, so
    a builder that delegates to another builder (``build_vios_document`` ->
    ``build_lpar_document``) cannot double-escape its caller's value.
    """

    __slots__ = ()


def escape_xml(value: str) -> str:
    """Escape a caller-supplied value for interpolation into an XML template.

    The result is safe as element character data and as a quoted attribute
    value. Escaping an already-escaped value returns it unchanged.
    """
    if isinstance(value, _EscapedXmlText):
        return value
    return _EscapedXmlText(escape(value, _ATTRIBUTE_ENTITIES))


def _escape_item(value: object) -> object:
    """Escape a string; leave any other type alone."""
    return escape_xml(value) if isinstance(value, str) else value


def _escape_argument(value: object) -> object:
    """Escape the strings in one builder argument, including list and dict members."""
    if isinstance(value, list):
        return [_escape_item(item) for item in value]
    if isinstance(value, dict):
        return {_escape_item(key): _escape_item(item) for key, item in value.items()}
    return _escape_item(value)


_P = ParamSpec("_P")


def escapes_string_arguments(func: Callable[_P, str]) -> Callable[_P, str]:
    """Escape every string a caller passes, before the builder interpolates it.

    Escaping at the boundary rather than at each interpolation site is what
    makes the document builders correct by construction: the function body
    never holds an unescaped caller value, so the number of times it
    interpolates one does not matter. Strings inside lists and dicts are
    escaped too, which covers ``physical_volumes`` and the job-parameter
    mapping; every other argument type is passed through untouched.

    A closed-vocabulary argument is escaped before the builder validates it,
    so a rejection message quotes the escaped form of an illegal value.
    """

    @functools.wraps(func)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> str:
        escaped_args = cast(
            "_P.args", tuple(_escape_argument(value) for value in args)
        )
        escaped_kwargs = cast(
            "_P.kwargs",
            {name: _escape_argument(value) for name, value in kwargs.items()},
        )
        return func(*escaped_args, **escaped_kwargs)

    # The marker the escaping harness reads to prove every builder is wrapped;
    # a type checker cannot model an attribute set on a function object.
    wrapper.__xml_escaped_arguments__ = True  # ty: ignore[unresolved-attribute]
    return wrapper


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
