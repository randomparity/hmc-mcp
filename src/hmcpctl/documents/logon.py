"""LogonRequest document for the session the client opens before every operation."""

from __future__ import annotations

from ..xmlutil import WEB_NS, escapes_string_arguments
from .common import document_envelope


@escapes_string_arguments
def build_logon_request_document(user: str, password: str) -> str:
    """LogonRequest document carrying the configured HMC credentials (PUT).

    The credentials arrive from ``HMCConfig`` rather than from a tool
    argument, which is why they reach this boundary as an explicit builder
    call instead of through a decorator on the client method: an
    argument-boundary decorator on ``HMCClient.logon`` would never see them.
    """
    body = f"""  <Metadata>
    <Atom/>
  </Metadata>
  <UserID kb="CUR" kxe="false">{user}</UserID>
  <Password kb="CUR" kxe="false">{password}</Password>"""
    return document_envelope("LogonRequest", body, WEB_NS)
