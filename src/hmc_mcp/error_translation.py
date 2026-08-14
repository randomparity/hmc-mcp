"""Presentation-neutral translations for narrowly identified HMC failures."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from .errors import HMCError

_T = TypeVar("_T")


async def run_with_error_translation(
    operation: Callable[[], Awaitable[_T]], translator: Callable[[HMCError], None]
) -> _T:
    """Run one client operation and apply a narrowly scoped HMC translator."""
    try:
        return await operation()
    except HMCError as exc:
        translator(exc)
        raise


def translate_pcm_error(exc: HMCError) -> None:
    if exc.status_code == 406:
        raise HMCError(
            "PCM is not licensed or not enabled on this HMC. "
            "Enable PCM in the HMC settings or use an HMC that has the PCM feature licensed.",
            exc.status_code,
        ) from exc
    if exc.status_code == 403:
        raise HMCError(
            "The connecting user does not have PCM authority on this HMC. "
            "Grant the user PCM authority in HMC user management and retry.",
            exc.status_code,
        ) from exc


def translate_template_error(exc: HMCError) -> None:
    if exc.status_code == 406:
        raise HMCError(
            "Partition templates are not licensed or not supported on this HMC. "
            "Enable the partition template feature in HMC settings or use an HMC with the feature licensed.",
            exc.status_code,
        ) from exc


def translate_virtual_network_create_error(exc: HMCError) -> None:
    if exc.status_code == 406:
        raise HMCError(
            "The HMC rejected the virtual network create request (Not Acceptable). "
            "Likely causes: (1) Accept or Content-Type header mismatch — "
            "the HMC may require a more specific media type; "
            "(2) XML schema version mismatch — try setting "
            "HMC_SCHEMA_VERSION=V1_0 in the environment and retrying.",
            exc.status_code,
        ) from exc
