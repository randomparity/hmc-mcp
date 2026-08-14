"""Local MCP tool collection for explicit application composition."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations


@dataclass(frozen=True)
class ToolDefinition:
    handler: Callable[..., Any]
    annotations: ToolAnnotations | None


def tool_module():
    """Return a module-local decorator and explicit registration function."""
    definitions: list[ToolDefinition] = []

    def tool(
        handler: Callable[..., Any] | None = None,
        *,
        annotations: ToolAnnotations | None = None,
    ):
        def collect(fn: Callable[..., Any]):
            definitions.append(ToolDefinition(fn, annotations))
            return fn

        return collect(handler) if handler is not None else collect

    def register_tools(mcp: FastMCP) -> None:
        for definition in definitions:
            mcp.tool(definition.handler, annotations=definition.annotations)

    return tool, register_tools
