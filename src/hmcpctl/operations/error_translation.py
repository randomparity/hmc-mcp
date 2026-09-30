"""Presentation-neutral translations for narrowly identified HMC failures."""

from __future__ import annotations

from ..errors import HMCError


def translate_pcm_error(exc: HMCError) -> HMCError:
    if exc.status_code == 406:
        return HMCError(
            "PCM is not licensed or not enabled on this HMC. "
            "Enable PCM in the HMC settings or use an HMC that has the PCM feature licensed.",
            exc.status_code,
            body=exc.body,
        )
    if exc.status_code == 403:
        return HMCError(
            "The connecting user does not have PCM authority on this HMC. "
            "Grant the user PCM authority in HMC user management and retry.",
            exc.status_code,
            body=exc.body,
        )
    return exc


def translate_template_error(exc: HMCError) -> HMCError:
    if exc.status_code == 406:
        return HMCError(
            "Partition templates are not licensed or not supported on this HMC. "
            "Enable the partition template feature in HMC settings or use an HMC with the feature licensed.",
            exc.status_code,
            body=exc.body,
        )
    return exc


def translate_virtual_network_create_error(exc: HMCError) -> HMCError:
    if exc.status_code == 406:
        return HMCError(
            "The HMC rejected the virtual network create request (Not Acceptable). "
            "Likely causes: (1) media-type negotiation — hmcpctl sends Accept */* "
            "with a typed Content-Type, so this HMC level negotiates differently; "
            "(2) the X-HMC-Schema-Version request header — some HMC levels reject it on "
            "particular endpoints and hmcpctl decides per call site whether to send it, "
            "so see docs/compatibility.md before changing any schema setting.",
            exc.status_code,
            body=exc.body,
        )
    return exc
