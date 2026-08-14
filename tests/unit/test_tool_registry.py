"""Direct contract tests for module-local MCP tool collection."""

from __future__ import annotations

import asyncio

from mcp.types import ToolAnnotations

from hmc_mcp._app import create_mcp
from hmc_mcp.tool_registry import tool_module


def _tool_names(application) -> set[str]:
    return {tool.name for tool in asyncio.run(application.list_tools())}


def test_tool_modules_collect_definitions_in_isolation():
    first_tool, first_register = tool_module()
    second_tool, second_register = tool_module()

    @first_tool
    def first_handler() -> str:
        return "first"

    @second_tool
    def second_handler() -> str:
        return "second"

    first_application = create_mcp()
    second_application = create_mcp()
    first_register(first_application)
    second_register(second_application)

    assert _tool_names(first_application) == {"first_handler"}
    assert _tool_names(second_application) == {"second_handler"}


def test_tool_module_preserves_annotations_and_decorated_handler():
    tool, register_tools = tool_module()
    read_only = ToolAnnotations(readOnlyHint=True)

    @tool(annotations=read_only)
    def status() -> str:
        return "ok"

    application = create_mcp()
    register_tools(application)
    registered = asyncio.run(application.list_tools())

    assert status() == "ok"
    assert len(registered) == 1
    assert registered[0].name == "status"
    assert registered[0].annotations == read_only


def test_same_definitions_register_on_independent_applications():
    tool, register_tools = tool_module()

    @tool
    def ping() -> str:
        return "pong"

    first = create_mcp()
    second = create_mcp()
    register_tools(first)
    register_tools(second)

    assert _tool_names(first) == {"ping"}
    assert _tool_names(second) == {"ping"}
