"""hmc_search_tools and hmc_invoke_tool over MCP (#1219, ADR 0189 Decision 3)."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterator

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from hmcpctl.audit import sink as audit_sink
from hmcpctl.authorization.access_policy import compile_access_policy
from hmcpctl.authorization.dispatch_scope import dispatch_authorizer
from hmcpctl.server import TOOL_SECURITY, create_mcp
from hmcpctl.server_tools.command import configure_arbitrary_command_tool

SEARCH = "hmc_search_tools"
INVOKE = "hmc_invoke_tool"
TIER_META_KEY = "io.github.randomparity.hmcpctl/catalog-tier"
_GATEWAY = [SEARCH, INVOKE]
_STATUS = "hmc_operation_status"


@pytest.fixture(autouse=True)
def environment_only_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep build_config off a developer's own TOML profiles (AGENTS.md, HMC_* leaks)."""
    monkeypatch.setenv("HMC_HOST", "hmc.test")
    monkeypatch.delenv("HMC_AGENT_ID", raising=False)


@pytest.fixture
def records() -> Iterator[list[dict]]:
    """Every authorization audit record emitted during the test, parsed."""
    parsed: list[dict] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            parsed.append(json.loads(record.getMessage()))

    logger = logging.getLogger(audit_sink.AUDIT_LOGGER_NAME)
    handler, level, propagate = _Collect(), logger.level, logger.propagate
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    yield parsed
    logger.removeHandler(handler)
    logger.setLevel(level)
    logger.propagate = propagate


def _policy(*grants: dict):
    return compile_access_policy(
        {"policies": {"test": {"grants": list(grants)}}},
        "test",
        TOOL_SECURITY,
        "test-tool-gateway.toml",
    )


def _grant(*tools: str, connections: tuple[str, ...] = ("<default>",)) -> dict:
    return {
        "tools": list(tools),
        "connections": list(connections),
        "targets": "all-targets",
    }


def _app(*tools: str) -> FastMCP:
    return create_mcp(_policy(_grant(*_GATEWAY, *tools)))


def _call(app: FastMCP, name: str, arguments: dict):
    async def go():
        async with Client(app) as client:
            return (await client.call_tool(name, arguments)).structured_content

    return asyncio.run(go())


def _listed(app: FastMCP):
    async def go():
        async with Client(app) as client:
            return {tool.name: tool for tool in await client.list_tools()}

    return asyncio.run(go())


def _names(result: dict) -> list[str]:
    return [entry["name"] for entry in result["tools"]]


def test_security_classification_is_conservative():
    search, invoke = TOOL_SECURITY[SEARCH], TOOL_SECURITY[INVOKE]
    assert (search.effect, search.operation) == ("read", "tools.search")
    assert (invoke.effect, invoke.operation) == ("destructive", "tools.invoke")
    listed = _listed(_app())
    assert listed[SEARCH].annotations.read_only_hint is True
    assert listed[INVOKE].annotations.destructive_hint is True


def test_search_by_query_ranks_bounded_matches_without_schemas():
    app = create_mcp(
        _policy(
            _grant(
                SEARCH,
                "hmc_power_on_lpar",
                "hmc_power_off_lpar",
                "hmc_list_lpars",
                "hmc_list_systems",
            )
        )
    )
    result = _call(app, SEARCH, {"query": "power off lpar", "limit": 1})
    assert _names(result) == ["hmc_power_off_lpar"]
    assert (result["limit"], result["truncated"]) == (1, True)
    entry = result["tools"][0]
    assert entry["effect"] == "destructive"
    assert entry["summary"] and "\n" not in entry["summary"]
    assert set(entry["maturity"]) >= {"implementation", "verification"}
    assert entry["input_schema"] is None


def test_search_by_exact_name_returns_its_full_input_schema():
    app = _app(_STATUS)
    result = _call(app, SEARCH, {"name": _STATUS})
    (entry,) = result["tools"]
    assert entry["name"] == _STATUS
    assert entry["input_schema"] == _listed(app)[_STATUS].input_schema


def test_search_sees_only_what_the_policy_exposes():
    app = _app(_STATUS)
    assert _call(app, SEARCH, {"name": "hmc_power_off_lpar"})["tools"] == []
    assert "hmc_power_off_lpar" not in _names(
        _call(app, SEARCH, {"query": "power off partition", "limit": 20})
    )


def test_search_with_no_match_is_an_empty_page():
    result = _call(_app(), SEARCH, {"query": "zzzqqq"})
    assert result == {"tools": [], "limit": 10, "truncated": False}


def test_search_omits_the_arbitrary_command_tool_unless_enabled():
    policy = _policy(_grant(SEARCH, "hmc_run_command"))
    app = create_mcp(policy)
    assert _call(app, SEARCH, {"name": "hmc_run_command"})["tools"] == []
    asyncio.run(
        configure_arbitrary_command_tool(
            True,
            app,
            permits=policy.permits_tool,
            authorize=dispatch_authorizer(policy),
        )
    )
    assert _names(_call(app, SEARCH, {"name": "hmc_run_command"})) == [
        "hmc_run_command"
    ]


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        ({}, "invalid_arguments: "),
        ({"query": "power", "name": _STATUS}, "invalid_arguments: "),
        ({"query": ""}, "invalid_query: "),
        ({"query": "hmc --"}, "invalid_query: "),
        ({"query": "x" * 201}, "invalid_query: "),
        ({"query": "power", "limit": 0}, "invalid_limit: "),
        ({"query": "power", "limit": 21}, "invalid_limit: "),
    ],
)
def test_malformed_searches_are_tool_errors(arguments, reason):
    with pytest.raises(ToolError, match=reason):
        _call(_app(), SEARCH, arguments)


def test_invoke_returns_the_invoked_tools_own_result(records):
    app = _app(_STATUS)
    direct = _call(app, _STATUS, {"limit": 3})
    records.clear()
    assert _call(app, INVOKE, {"name": _STATUS, "arguments": {"limit": 3}}) == {
        "name": _STATUS,
        "result": direct,
    }
    assert [(r["tool"], r["decision"]) for r in records] == [
        (INVOKE, "allow"),
        (_STATUS, "allow"),
    ]


def test_invoke_reenters_the_invoked_tools_authorization(records):
    app = create_mcp(_policy(_grant(*_GATEWAY), _grant(_STATUS, connections=("lab",))))
    with pytest.raises(ToolError, match="access policy"):
        _call(app, INVOKE, {"name": _STATUS, "arguments": {}})
    assert (records[-1]["tool"], records[-1]["decision"]) == (_STATUS, "deny")


def test_invoke_runs_the_invoked_tools_argument_validation():
    with pytest.raises(ToolError, match="invalid_limit: "):
        _call(_app(_STATUS), INVOKE, {"name": _STATUS, "arguments": {"limit": 0}})
    with pytest.raises(ToolError, match="(?i)validation error"):
        _call(_app(_STATUS), INVOKE, {"name": _STATUS, "arguments": {"limit": "many"}})


def test_unknown_withheld_and_disabled_names_get_one_denial():
    policy = _policy(_grant(*_GATEWAY))
    app = create_mcp(policy)
    messages = set()
    for name in ("hmc_no_such_tool", "hmc_power_off_lpar", "hmc_run_command"):
        with pytest.raises(ToolError) as error:
            _call(app, INVOKE, {"name": name, "arguments": {}})
        messages.add(str(error.value))
    assert len(messages) == 1
    assert "tool_unavailable: " in next(iter(messages))


@pytest.mark.parametrize("name", _GATEWAY)
def test_invoke_refuses_the_gateway_tools(name):
    with pytest.raises(ToolError, match="gateway_recursion: "):
        _call(_app(), INVOKE, {"name": name, "arguments": {}})


def test_invoke_refuses_an_enabled_arbitrary_command_tool():
    policy = _policy(_grant(*_GATEWAY, "hmc_run_command"))
    app = create_mcp(policy)
    asyncio.run(
        configure_arbitrary_command_tool(
            True,
            app,
            permits=policy.permits_tool,
            authorize=dispatch_authorizer(policy),
        )
    )
    with pytest.raises(ToolError, match="arbitrary_command_refused: "):
        _call(
            app, INVOKE, {"name": "hmc_run_command", "arguments": {"cmd": "lshmc -V"}}
        )


def test_invoke_refuses_oversized_arguments_before_dispatch(records):
    app = _app(_STATUS)
    records.clear()
    with pytest.raises(ToolError, match="arguments_too_large: "):
        _call(app, INVOKE, {"name": _STATUS, "arguments": {"cursor": "x" * 65_536}})
    assert [r["tool"] for r in records] == [INVOKE]


def test_gateway_tools_are_absent_when_the_policy_withholds_them():
    listed = _listed(create_mcp(_policy(_grant(_STATUS))))
    assert set(listed) == {_STATUS}


def test_listing_marks_primary_tools_without_changing_the_listing():
    listed = _listed(_app("hmc_provision_lpar", "hmc_list_lpars", _STATUS))
    assert set(listed) == {*_GATEWAY, "hmc_provision_lpar", "hmc_list_lpars", _STATUS}
    tiers = {name: tool.meta[TIER_META_KEY] for name, tool in listed.items()}
    assert tiers == {
        SEARCH: "primary",
        INVOKE: "primary",
        "hmc_provision_lpar": "primary",
        _STATUS: "primary",
        "hmc_list_lpars": "secondary",
    }
