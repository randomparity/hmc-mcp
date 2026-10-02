"""hmc_operation_status over MCP: agent isolation, paging and no HMC traffic (#1218)."""

from __future__ import annotations

import asyncio

import pytest
import respx
from fastmcp import Client
from fastmcp.exceptions import ToolError

from hmcpctl.authorization.access_policy import DEFAULT_CONNECTION_TOKEN
from hmcpctl.cli_commands.legacy_policy import compile_legacy_policy
from hmcpctl.operations.logical import store
from hmcpctl.server import TOOL_SECURITY, create_mcp

APP = create_mcp(compile_legacy_policy(TOOL_SECURITY, (DEFAULT_CONNECTION_TOKEN,)))


@pytest.fixture(autouse=True)
def environment_only_config(monkeypatch):
    """Keep build_config off a developer's own TOML profiles (AGENTS.md, HMC_* leaks)."""
    monkeypatch.setenv("HMC_HOST", "hmc.test")


def _call(arguments: dict):
    async def go():
        async with Client(APP) as client:
            return (
                await client.call_tool("hmc_operation_status", arguments)
            ).structured_content

    return asyncio.run(go())


def _seed(agent: str, n: int) -> str:
    operation_id = f"{n:032x}"
    with store.session() as conn, store.write_transaction(conn):
        store.insert_operation(
            conn,
            operation_id=operation_id,
            agent_id=agent,
            request_id=f"r{n}",
            tool="hmc_test_tool",
            connection="<default>",
            host="hmc.test",
            digest="d" * 64,
            request_json='{"arguments":{"hidden_marker":"zz"}}',
        )
    return operation_id


def test_security_classification():
    security = TOOL_SECURITY["hmc_operation_status"]
    assert (security.effect, security.operation, security.target_kind) == (
        "read",
        "operation.status",
        "console",
    )
    assert security.connection_argument == "profile"
    assert security.exhaustive_targets is False


def test_returns_only_the_callers_records_without_hmc_traffic(monkeypatch):
    monkeypatch.setenv("HMC_AGENT_ID", "agent-a")
    mine = _seed("agent-a", 1)
    _seed("agent-b", 2)
    with respx.mock(assert_all_called=False) as router:
        page = _call({})
    assert router.calls.call_count == 0
    assert [r["operation_id"] for r in page["operations"]] == [mine]
    assert "hidden_marker" not in str(page)


def test_default_agent_id_is_hmcpctl():
    _seed("hmcpctl", 1)
    assert len(_call({})["operations"]) == 1


def test_empty_page_without_a_store():
    page = _call({"limit": 5})
    assert page == {
        "operations": [],
        "limit": 5,
        "truncated": False,
        "next_cursor": None,
    }
    assert not store.state_dir().exists()


def test_pages_through_next_cursor(monkeypatch):
    monkeypatch.setenv("HMC_AGENT_ID", "agent-a")
    for n in range(3):
        _seed("agent-a", n)
    first = _call({"limit": 2})
    rest = _call({"limit": 2, "cursor": first["next_cursor"]})
    assert len(first["operations"]) + len(rest["operations"]) == 3
    assert rest["truncated"] is False


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        ({"limit": 0}, "invalid_limit: "),
        ({"operation_id": "nope"}, "invalid_operation_id: "),
        ({"cursor": "!!!"}, "invalid_cursor: "),
        ({"state": "odd"}, r"(?s)\bstate\b.*Input should be 'running', 'interrupted'"),
    ],
)
def test_invalid_arguments_are_tool_errors(arguments, reason):
    with pytest.raises(ToolError, match=reason):
        _call(arguments)
