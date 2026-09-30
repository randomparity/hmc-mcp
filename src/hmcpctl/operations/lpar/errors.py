"""Shared translation for HMC LPAR write rejections."""

from ...errors import HMCError


def translate_lpar_write_error(exc: HMCError) -> HMCError:
    """Translate an LPAR write rejection while preserving its response body."""
    if exc.status_code == 406:
        return HMCError(
            "The HMC rejected the LPAR write request (Not Acceptable). "
            "Likely causes: (1) media-type negotiation — hmcpctl sends Accept */* "
            "with a typed Content-Type, so this HMC level negotiates differently; "
            "(2) the X-HMC-Schema-Version request header — some HMC levels reject it on "
            "particular endpoints, and hmcpctl decides per call site whether to send it, "
            "so report this as a client defect rather than changing environment settings.",
            exc.status_code,
            body=exc.body,
        )
    return exc
