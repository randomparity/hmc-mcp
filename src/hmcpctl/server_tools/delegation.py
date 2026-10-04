"""Authorize one delegated specialist call for a logical tool (ADR 0189 Decision 2)."""

from __future__ import annotations

from collections.abc import Mapping

from ..tool_registry import Authorize, ToolSecurity


def authorize_as(
    tool_security: Mapping[str, ToolSecurity],
    authorize: Authorize,
    name: str,
    by_kind: Mapping[str, str | None],
    profile: str | None,
) -> None:
    """Authorize a call as tool *name*, its targets drawn from *by_kind* by target kind.

    A target kind absent from *by_kind* is passed as ``None``. Raises what *authorize*
    raises; the caller decides whether a denial refuses the call or becomes a result.
    """
    security = tool_security[name]
    arguments = {
        target.argument: by_kind.get(target.kind) for target in security.targets
    }
    if security.connection_argument is not None:
        arguments[security.connection_argument] = profile
    authorize(name, security, arguments)
