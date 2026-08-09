"""Parse helpers that tag XML failures with the HMC call that returned them."""

from __future__ import annotations

from typing import Any

from defusedxml import ElementTree as DET

from .errors import HMCError
from .pcm import metric_links, pcm_preferences_to_dict
from .xmlutil import find_text, parse_feed


def _parse_feed(xml_text: str, context: str) -> list[dict[str, Any]]:
    """parse_feed that tags XML failures with the HMC call that returned it."""
    try:
        return parse_feed(xml_text)
    except DET.ParseError as exc:
        raise HMCError(f"Failed to parse {context} response") from exc


def _find_text(xml_text: str, context: str, *names: str) -> str | None:
    """find_text that tags XML failures with the HMC call that returned it."""
    try:
        return find_text(xml_text, *names)
    except DET.ParseError as exc:
        raise HMCError(f"Failed to parse {context} response") from exc


def _metric_links(xml_text: str, context: str) -> list[dict[str, str]]:
    """metric_links that tags XML failures with the HMC call that returned it."""
    try:
        return metric_links(xml_text)
    except DET.ParseError as exc:
        raise HMCError(f"Failed to parse {context} response") from exc


def _pcm_preferences(xml_text: str, context: str) -> dict[str, Any]:
    """pcm_preferences_to_dict that tags XML failures with the HMC call that returned it."""
    try:
        return pcm_preferences_to_dict(xml_text)
    except DET.ParseError as exc:
        raise HMCError(f"Failed to parse {context} response") from exc
