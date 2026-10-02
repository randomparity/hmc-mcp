"""Response-time operation-maturity and catalog-tier metadata for MCP tool discovery."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from datetime import UTC, datetime

import mcp_types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import Tool

from hmcpctl.operation_maturity import operation_maturity_meta
from hmcpctl.tool_registry import ToolSecurity

MATURITY_META_KEY = "io.github.randomparity.hmcpctl/operation-maturity"
# ADR 0189 Decision 4: "primary" or "secondary". Metadata only; the listing itself
# is unchanged until #1232.
TIER_META_KEY = "io.github.randomparity.hmcpctl/catalog-tier"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class OperationMaturityMiddleware(Middleware):
    """Add maturity and catalog-tier metadata to the tools returned by ``tools/list``."""

    def __init__(
        self,
        tool_security: Mapping[str, ToolSecurity],
        *,
        primary_tools: Collection[str] = frozenset(),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._tool_security = tool_security
        self._primary_tools = primary_tools
        self._clock = clock or _utc_now

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        tools = await call_next(context)
        now = self._clock()
        projected: list[Tool] = []
        for tool in tools:
            security = self._tool_security.get(tool.name)
            if security is None:
                projected.append(tool)
                continue
            metadata = dict(tool.meta or {})
            metadata[MATURITY_META_KEY] = operation_maturity_meta(
                security.operation, now=now
            )
            metadata[TIER_META_KEY] = (
                "primary" if tool.name in self._primary_tools else "secondary"
            )
            projected.append(tool.model_copy(update={"meta": metadata}))
        return projected
