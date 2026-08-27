"""Parse helpers that tag XML failures with the HMC call that returned them."""

from __future__ import annotations

import functools
from typing import Any, Callable, TypeVar

from defusedxml import ElementTree as DET

from ..errors import HMCError
from ..pcm import metric_links, pcm_preferences_to_dict
from ..xmlutil import find_text, parse_feed

_T = TypeVar("_T")


def _tag_parse_errors(fn: Callable[..., _T]) -> Callable[..., _T]:
    """Wrap an XML parse callable so a ParseError becomes an HMCError.

    The wrapper takes the same arguments as *fn* with a *context* string
    inserted second; on a parse failure it raises ``HMCError(f"Failed to
    parse {context} response")`` so the error names the HMC call that
    returned the malformed XML.
    """

    @functools.wraps(fn)
    def wrapper(xml_text: str, context: str, *args: Any) -> _T:
        try:
            return fn(xml_text, *args)
        except DET.ParseError as exc:
            raise HMCError(
                f"Failed to parse {context} response: {str(exc)[:500]}"
            ) from exc

    return wrapper


_parse_feed = _tag_parse_errors(parse_feed)
_find_text = _tag_parse_errors(find_text)
_metric_links = _tag_parse_errors(metric_links)
_pcm_preferences = _tag_parse_errors(pcm_preferences_to_dict)
