"""Response-time operation-maturity metadata for MCP tool discovery."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime

import mcp_types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import Tool

from hmc_mcp.operation_maturity import operation_maturity_meta
from hmc_mcp.tool_registry import ToolSecurity

MATURITY_META_KEY = "io.github.randomparity.hmc-mcp/operation-maturity"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class OperationMaturityMiddleware(Middleware):
    """Add current maturity metadata to the tools returned by ``tools/list``."""

    def __init__(
        self,
        tool_security: Mapping[str, ToolSecurity],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._tool_security = tool_security
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
            projected.append(tool.model_copy(update={"meta": metadata}))
        return projected
