"""hmc_inventory over MCP under real access policies (#1220, ADR 0196)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import patch

import pytest
from fastmcp import Client, FastMCP

from hmcpctl.audit import sink as audit_sink
from hmcpctl.authorization.access_policy import compile_access_policy
from hmcpctl.operations.inventory.logical import DELEGATED_TOOLS
from hmcpctl.server import TOOL_SECURITY, create_mcp

INVENTORY = "hmc_inventory"
SYS_A = "0000000a-0000-4000-8000-000000000000"
SYS_B = "0000000b-0000-4000-8000-000000000000"


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


def _system(uuid: str, name: str) -> dict:
    return {
        "UUID": uuid,
        "Resource": {
            "SystemName": name,
            "State": "operating",
            "AssociatedSystemMemoryConfiguration": {
                "ConfigurableSystemMemory": "1024",
                "CurrentAvailableSystemMemory": "512",
            },
            "AssociatedSystemProcessorConfiguration": {
                "ConfigurableSystemProcessorUnits": "4",
                "CurrentAvailableSystemProcessorUnits": "2",
            },
        },
    }


def _lpar(system_uuid: str, name: str) -> dict:
    return {
        "UUID": system_uuid[:-1] + "1",
        "Resource": {
            "PartitionName": name,
            "PartitionState": "running",
            "Description": "[hmcpctl owner:agent-a created:2026-10-01]",
        },
    }


class _HMC:
    def __init__(self) -> None:
        self.systems = {SYS_A: _system(SYS_A, "sysA"), SYS_B: _system(SYS_B, "sysB")}
        self.calls: list[str] = []

    async def list_managed_systems(self) -> list[dict]:
        self.calls.append("list_managed_systems")
        return list(self.systems.values())

    async def get_managed_system(self, uuid: str) -> dict | None:
        return self.systems.get(uuid)

    async def find_system_by_name(self, name: str) -> dict | None:
        self.calls.append(f"find_system_by_name {name}")
        return next(
            (s for s in self.systems.values() if s["Resource"]["SystemName"] == name),
            None,
        )

    async def list_logical_partitions(self, uuid: str) -> list[dict]:
        self.calls.append(f"list_logical_partitions {uuid}")
        return [_lpar(uuid, f"web-{uuid[7]}")]


def _app(*grants: dict) -> FastMCP:
    policy = compile_access_policy(
        {"policies": {"test": {"grants": list(grants)}}},
        "test",
        TOOL_SECURITY,
        "test-logical-inventory.toml",
    )
    return create_mcp(policy)


def _grant(*tools: str, targets: Any = "all-targets") -> dict:
    return {"tools": list(tools), "connections": ["<default>"], "targets": targets}


def _call(app: FastMCP, hmc: _HMC, arguments: dict) -> dict:
    @contextlib.asynccontextmanager
    async def client(*_args: Any, **_kwargs: Any) -> AsyncIterator[_HMC]:
        yield hmc

    async def go() -> dict:
        async with Client(app) as mcp_client:
            result = await mcp_client.call_tool(INVENTORY, arguments)
            assert result.structured_content is not None
            return result.structured_content

    with patch("hmcpctl._app.client_from_env", side_effect=client):
        return asyncio.run(go())


def _listed(app: FastMCP) -> dict:
    async def go() -> dict:
        async with Client(app) as client:
            return {tool.name: tool for tool in await client.list_tools()}

    return asyncio.run(go())


_ALL = _grant(INVENTORY, *DELEGATED_TOOLS)
# The inventory's own grant is all-targets (ADR 0196 Decision 4); the partition and
# ownership tools carry the target bound.
_SCOPED = (
    _grant(INVENTORY),
    _grant(
        "hmc_list_lpars",
        "hmc_list_lpar_ownership",
        targets={"managed_system": ["sysA"]},
    ),
)


def test_inventory_is_read_console_and_not_exhaustive():
    security = TOOL_SECURITY[INVENTORY]
    assert (security.effect, security.target_kind) == ("read", "console")
    assert security.exhaustive_targets is False
    assert security.connection_argument == "profile"


def test_delegated_tools_are_registered_names():
    assert set(DELEGATED_TOOLS) <= set(TOOL_SECURITY)


def test_all_targets_policy_returns_every_source_ok():
    page = _call(_app(_ALL), _HMC(), {})
    assert page["systems_source"]["status"] == "ok"
    assert [s["id"] for s in page["systems"]] == [
        f"<default>/{SYS_A}",
        f"<default>/{SYS_B}",
    ]
    for system in page["systems"]:
        assert {v["status"] for v in system["sources"].values()} == {"ok"}
    assert {p["owner"] for p in page["partitions"]} == {"agent-a"}


def test_targets_table_without_selectors_reads_nothing():
    hmc = _HMC()
    page = _call(_app(*_SCOPED), hmc, {})
    assert page["systems_source"]["status"] == "denied"
    assert "Pass systems selectors" in page["systems_source"]["detail"]
    assert page["systems"] == [] and page["partitions"] == []
    assert hmc.calls == []


def test_targets_table_admits_each_selector_separately():
    hmc = _HMC()
    page = _call(_app(*_SCOPED), hmc, {"systems": ["sysA", "sysB"]})
    by_selector = {s["selector"]: s for s in page["systems"]}
    allowed, denied = by_selector["sysA"], by_selector["sysB"]
    assert allowed["sources"]["partitions"]["status"] == "ok"
    assert allowed["sources"]["ownership"]["status"] == "ok"
    # hmc_capacity_report is not granted, so capacity is withheld everywhere.
    assert allowed["sources"]["capacity"]["status"] == "denied"
    assert allowed["total_memory_mib"] is None
    assert denied["sources"]["partitions"]["status"] == "denied"
    assert (denied["id"], denied["uuid"], denied["name"], denied["state"]) == (
        None,
        None,
        None,
        None,
    )
    assert "find_system_by_name sysB" not in hmc.calls
    assert [p["system_id"] for p in page["partitions"]] == [f"<default>/{SYS_A}"]


def test_withheld_ownership_tool_is_denied_and_owner_null():
    app = _app(_grant(INVENTORY, "hmc_list_systems", "hmc_list_lpars"))
    page = _call(app, _HMC(), {})
    for system in page["systems"]:
        assert system["sources"]["ownership"]["status"] == "denied"
        assert "hmc_list_lpar_ownership" in system["sources"]["ownership"]["detail"]
    assert {(p["owned"], p["owner"]) for p in page["partitions"]} == {(None, None)}


def test_each_delegated_decision_is_recorded_under_its_tool(records):
    _call(_app(*_SCOPED), _HMC(), {"systems": ["sysA", "sysB"]})
    decisions = {(r["tool"], r["decision"]) for r in records}
    assert (INVENTORY, "allow") in decisions
    assert ("hmc_list_lpars", "allow") in decisions
    assert ("hmc_list_lpars", "deny") in decisions
    assert ("hmc_list_lpar_ownership", "allow") in decisions


def test_inventory_is_listed_as_primary():
    listed = _listed(_app(_ALL))
    meta = listed[INVENTORY].meta or {}
    assert meta.get("io.github.randomparity.hmcpctl/catalog-tier") == "primary"
