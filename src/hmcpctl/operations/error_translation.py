"""Presentation-neutral translations for narrowly identified HMC failures."""

from __future__ import annotations

from ..errors import HMCError


def _not_acceptable(feature: str, exc: HMCError) -> HMCError:
    """Explain an HTTP 406 as the media-type refusal it is.

    A 406 means the HMC cannot answer in the media type the Accept header named
    (docs/refs/hmc-rest-api-p10/http-protocol/014-request-headers.md); V10R3
    returns it for a typed Accept on templates and PCM preferences (#1202).
    """
    return HMCError(
        f"The HMC refused the media type hmcpctl requested for {feature} "
        "(HTTP 406 Not Acceptable). This is media-type negotiation, not "
        "licensing: report it as an hmcpctl defect, with the HMC version.",
        exc.status_code,
        body=exc.body,
    )


def translate_pcm_error(exc: HMCError) -> HMCError:
    if exc.status_code == 406:
        return _not_acceptable("PCM", exc)
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
        return _not_acceptable("partition templates", exc)
    return exc


def translate_virtual_network_create_error(exc: HMCError) -> HMCError:
    if exc.status_code == 406:
        return HMCError(
            "The HMC rejected the virtual network create request (Not Acceptable). "
            "Likely causes: (1) media-type negotiation — hmcpctl sends Accept */* "
            "with a typed Content-Type, so this HMC level negotiates differently; "
            "(2) the X-HMC-Schema-Version request header — some HMC levels reject it on "
            "particular endpoints, and hmcpctl decides per call site whether to send it, "
            "so report this as a client defect rather than changing environment settings.",
            exc.status_code,
            body=exc.body,
        )
    return exc
