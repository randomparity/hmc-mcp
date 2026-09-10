"""Operation-maturity discovery through the local CLI and MCP ``tools/list``."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import mcp_types as mt
import pytest
from fastmcp import Client
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import Tool
from typer.testing import CliRunner

from hmc_mcp import cli, operation_maturity_middleware
from hmc_mcp.authorization.access_policy import compile_access_policy
from hmc_mcp.cli_commands import capabilities as capability_commands
from hmc_mcp.cli_commands import runtime
from hmc_mcp.operation_maturity import OperationMaturity, operation_maturity_meta
from hmc_mcp.server import TOOL_SECURITY, create_mcp
from hmc_mcp.tool_registry import ToolSecurity

MATURITY_META_KEY = "io.github.randomparity.hmc-mcp/operation-maturity"
_NOW = datetime(2026, 9, 10, tzinfo=UTC)
_SOURCE = "test-capability-discovery.toml"


def _policy(*tool_names: str):
    return compile_access_policy(
        {
            "policies": {
                "test": {
                    "grants": [
                        {
                            "tools": list(tool_names),
                            "connections": ["<default>"],
                            "targets": "all-targets",
                        }
                    ]
                }
            }
        },
        "test",
        TOOL_SECURITY,
        _SOURCE,
    )


def _without_hmc_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "HMC_AGENT_ID",
        "HMC_AUDIT_MEMENTO",
        "HMC_AUTHORIZE_POWER_OPERATIONS",
        "HMC_HOST",
        "HMC_ISO_URL_ALLOWLIST",
        "HMC_PASSWORD",
        "HMC_PORT",
        "HMC_PROFILE",
        "HMC_SCHEMA_VERSION",
        "HMC_SSH_KEY_FILE",
        "HMC_SSH_TIMEOUT",
        "HMC_SSH_VERIFY_HOST_KEY",
        "HMC_TIMEOUT",
        "HMC_USER",
        "HMC_VERIFY_SSL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_cli_capability_rows_group_sorted_tools_and_project_once_per_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, datetime | None]] = []

    def project(operation: str, *, now: datetime | None = None) -> OperationMaturity:
        calls.append((operation, now))
        return OperationMaturity(
            implementation="implemented",
            verification="stale" if operation == "zeta.read" else "current",
            runtime_eligibility="existing-runtime-guards",
            reason="age-exceeded" if operation == "zeta.read" else None,
        )

    monkeypatch.setattr(capability_commands, "operation_maturity", project)
    security = {
        "tool_z": ToolSecurity("read", "zeta.read", "none"),
        "tool_b": ToolSecurity("read", "alpha.read", "none"),
        "tool_a": ToolSecurity("read", "alpha.read", "none"),
    }

    assert capability_commands.capability_rows(security, now=_NOW) == [
        {
            "operation": "alpha.read",
            "tools": ["tool_a", "tool_b"],
            "implementation": "implemented",
            "verification": "current",
            "runtime_eligibility": "existing-runtime-guards",
        },
        {
            "operation": "zeta.read",
            "tools": ["tool_z"],
            "implementation": "implemented",
            "verification": "stale",
            "runtime_eligibility": "existing-runtime-guards",
            "reason": "age-exceeded",
        },
    ]
    assert calls == [("alpha.read", _NOW), ("zeta.read", _NOW)]


def test_cli_capabilities_table_and_json_share_sorted_rows_without_hmc_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _without_hmc_settings(monkeypatch)

    def unexpected_client(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("capabilities must not create an HMC client")

    monkeypatch.setattr(runtime, "HMCClient", unexpected_client)
    monkeypatch.setattr(runtime, "build_config", unexpected_client)
    runner = CliRunner(env={"COLUMNS": "400"})

    json_result = runner.invoke(cli.app, ["capabilities", "--json"])
    table_result = runner.invoke(cli.app, ["capabilities"])

    assert json_result.exit_code == 0, json_result.output
    assert table_result.exit_code == 0, table_result.output
    rows = json.loads(json_result.stdout)
    assert rows == capability_commands.capability_rows(TOOL_SECURITY)
    operations = [row["operation"] for row in rows]
    assert operations == sorted(operations)
    assert "Operation" in table_result.stdout
    assert "Tools" in table_result.stdout
    assert "Implementation" in table_result.stdout
    assert "Verification" in table_result.stdout
    assert "Runtime eligibility" in table_result.stdout
    positions = [table_result.stdout.index(operation) for operation in operations]
    assert positions == sorted(positions)


def test_cli_capabilities_preserves_root_rejection_of_malformed_hmc_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HMC_VERIFY_SSL", "not-a-boolean")

    result = CliRunner().invoke(cli.app, ["capabilities"])

    assert result.exit_code == 2
    assert "Invalid value for '--verify-ssl'" in result.output


class _ExistingMetadata(Middleware):
    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        tools = await call_next(context)
        return [
            tool.model_copy(update={"meta": {"existing": "kept"}})
            if tool.name == "hmc_list_systems"
            else tool
            for tool in tools
        ]


def _listed_tools(application) -> dict[str, Any]:
    async def discover() -> dict[str, Any]:
        async with Client(application) as client:
            return {tool.name: tool for tool in await client.list_tools()}

    return asyncio.run(discover())


def test_mcp_discovery_adds_shared_maturity_after_ceiling_filtering() -> None:
    permitted = ("hmc_list_systems", "hmc_set_sriov_adapter_mode")
    application = create_mcp(_policy(*permitted))
    before = {
        tool.name: tool
        for tool in asyncio.run(application.list_tools(run_middleware=False))
    }
    application.add_middleware(_ExistingMetadata())

    discovered = _listed_tools(application)

    assert set(discovered) == set(permitted)
    for name, tool in discovered.items():
        operation = TOOL_SECURITY[name].operation
        assert tool.meta[MATURITY_META_KEY] == operation_maturity_meta(operation)
        assert tool.annotations == before[name].annotations
    assert discovered["hmc_list_systems"].meta["existing"] == "kept"
    assert "hmc_delete_lpar" not in discovered


def test_mcp_discovery_refreshes_maturity_for_each_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_at = _NOW - timedelta(days=90)
    current = [_NOW]

    def project(operation: str, *, now: datetime | None = None) -> dict[str, str]:
        assert operation == "system.list"
        assert now is not None
        return {
            "implementation": "implemented",
            "verification": "current"
            if now <= observed_at + timedelta(days=90)
            else "stale",
            "runtime_eligibility": "existing-runtime-guards",
        }

    monkeypatch.setattr(
        operation_maturity_middleware, "operation_maturity_meta", project
    )
    monkeypatch.setattr(operation_maturity_middleware, "_utc_now", lambda: current[0])
    application = create_mcp(_policy("hmc_list_systems"))

    first = _listed_tools(application)["hmc_list_systems"]
    current[0] += timedelta(microseconds=1)
    second = _listed_tools(application)["hmc_list_systems"]

    assert first.meta[MATURITY_META_KEY]["verification"] == "current"
    assert second.meta[MATURITY_META_KEY]["verification"] == "stale"


def test_mcp_and_cli_discovery_use_the_same_operation_evidence() -> None:
    name = "hmc_list_systems"
    operation = TOOL_SECURITY[name].operation
    row = next(
        row
        for row in capability_commands.capability_rows({name: TOOL_SECURITY[name]})
        if row["operation"] == operation
    )
    tool = _listed_tools(create_mcp(_policy(name)))[name]

    assert tool.meta[MATURITY_META_KEY] == {
        key: value for key, value in row.items() if key not in {"operation", "tools"}
    }
