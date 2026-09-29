"""Report the gap between the capability ledger and the live scenario registry.

Usage:
    python scripts/scenario_gap_report.py                     # report; exits 0
    python scripts/scenario_gap_report.py --fail-on-dispatch  # exit 1 on a dispatch finding

Joins ``docs/capabilities/operations.json`` and ``rows.json`` to the scenario
modules behind the runner's ``SUBTASKS`` registry, and checks each scenario
dispatch against the input schema the composed MCP application serves. Offline
and read-only: no HMC credential is needed and nothing is written.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import inspect
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import live_test_runner
from fastmcp import Client

from hmcpctl.authorization.access_policy import DEFAULT_CONNECTION_TOKEN
from hmcpctl.cli_commands.legacy_policy import compile_legacy_policy
from hmcpctl.server import TOOL_SECURITY, _gates, create_mcp
from hmcpctl.server_tools.command import configure_arbitrary_command_tool

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CAPABILITIES = _REPO_ROOT / "docs" / "capabilities"
# Keyword arguments `RunState.call` consumes itself; they never reach the tool.
_RUNNER_KEYWORDS = frozenset({"expected", "reuse_gaps"})
_DISPATCH_PREFIXES = ("unregistered:", "dispatch-mismatch:", "unreadable:")


@dataclass(frozen=True)
class Dispatch:
    """One ``call(client, "<tool>", ...)`` site: where, which tool, which keywords."""

    site: str
    tool: str
    arguments: tuple[str, ...]


@dataclass(frozen=True)
class Scan:
    """What one scenario module dispatches and names, plus what could not be read."""

    dispatches: tuple[Dispatch, ...]
    verified: tuple[tuple[str, str], ...]
    unreadable: tuple[str, ...]


def _literal(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def scan_source(source: str, label: str) -> Scan:
    """Read every dispatch and ``record_verified`` operation in one module's source."""
    dispatches: list[Dispatch] = []
    verified: list[tuple[str, str]] = []
    unreadable: list[str] = []
    calls = sorted(
        (node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call)),
        key=lambda node: (node.lineno, node.col_offset),
    )
    for node in calls:
        if not isinstance(node.func, ast.Attribute):
            continue
        site = f"{label}:{node.lineno}"
        if node.func.attr == "call":
            tool = _literal(node.args[1] if len(node.args) > 1 else None)
            names = [kw.arg for kw in node.keywords if kw.arg not in _RUNNER_KEYWORDS]
            if tool is None or None in names:
                unreadable.append(
                    f"{site} dispatch with a non-literal tool or a ** splat"
                )
                continue
            dispatches.append(Dispatch(site, tool, tuple(n for n in names if n)))
        elif node.func.attr == "record_verified":
            operation = next(
                (kw.value for kw in node.keywords if kw.arg == "operation"), None
            )
            name = _literal(operation)
            if name is None:
                unreadable.append(f"{site} record_verified without a literal operation")
                continue
            verified.append((site, name))
    return Scan(tuple(dispatches), tuple(verified), tuple(unreadable))


def build_report(
    scans: Sequence[Scan],
    operations: Sequence[Mapping[str, Any]],
    row_ids: Iterable[str],
    schemas: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    """Every finding as one prefixed line, followed by one ``summary:`` line."""
    by_tool = {entry["tool"]: entry["operation"] for entry in operations}
    known = {entry["operation"] for entry in operations}
    exercised: set[str] = set()
    unregistered: list[str] = []
    mismatches: list[str] = []
    unreadable: list[str] = []
    for scan in scans:
        unreadable += [f"unreadable: {item}" for item in scan.unreadable]
        for dispatch in scan.dispatches:
            if dispatch.tool not in schemas or dispatch.tool not in by_tool:
                unregistered.append(
                    f"unregistered: {dispatch.site} tool {dispatch.tool}"
                )
                continue
            exercised.add(by_tool[dispatch.tool])
            mismatches += [
                f"dispatch-mismatch: {dispatch.site} {problem}"
                for problem in live_test_runner._dispatch_problems(
                    dispatch.tool, dispatch.arguments, schemas
                )
            ]
        for site, operation in scan.verified:
            if operation in known:
                exercised.add(operation)
            else:
                unregistered.append(f"unregistered: {site} operation {operation}")
    covered_rows = {
        row
        for entry in operations
        if entry["operation"] in exercised
        for row in entry["row_ids"]
    }
    all_rows = sorted(set(row_ids))
    uncovered_operations = sorted(known - exercised)
    uncovered_rows = [row for row in all_rows if row not in covered_rows]
    summary = (
        f"summary: {sum(len(scan.dispatches) for scan in scans)} dispatches; "
        f"operations {len(known) - len(uncovered_operations)}/{len(known)} "
        f"exercised; rows {len(all_rows) - len(uncovered_rows)}/{len(all_rows)} "
        f"exercised; {len(unregistered)} unregistered; "
        f"{len(mismatches)} dispatch mismatches; {len(unreadable)} unreadable"
    )
    return [
        *(f"uncovered-operation: {name}" for name in uncovered_operations),
        *(f"uncovered-row: {row}" for row in uncovered_rows),
        *unregistered,
        *mismatches,
        *unreadable,
        summary,
    ]


def exit_status(lines: Iterable[str], *, fail_on_dispatch: bool) -> int:
    """1 only when asked to fail and a dispatch-level finding exists."""
    if fail_on_dispatch and any(line.startswith(_DISPATCH_PREFIXES) for line in lines):
        return 1
    return 0


async def served_schemas() -> dict[str, dict[str, Any]]:
    """The input schema each tool serves, composed exactly as the live runner does."""
    policy = compile_legacy_policy(
        TOOL_SECURITY, (DEFAULT_CONNECTION_TOKEN,), include_arbitrary_command=True
    )
    application = create_mcp(policy)
    permits, authorize = _gates(policy)
    await configure_arbitrary_command_tool(
        True, application, permits=permits, authorize=authorize
    )
    async with Client(application) as client:
        return {tool.name: tool.input_schema for tool in await client.list_tools()}


def scenario_scans() -> list[Scan]:
    """Scan each module that defines a registered ``SUBTASKS`` scenario."""
    paths = sorted(
        {Path(inspect.getfile(task)) for task in live_test_runner.SUBTASKS.values()}
    )
    return [
        scan_source(path.read_text(encoding="utf-8"), str(path.relative_to(_REPO_ROOT)))
        for path in paths
    ]


def _load(name: str, key: str) -> list[Any]:
    document = json.loads((_CAPABILITIES / name).read_text(encoding="utf-8"))
    return document[key]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--fail-on-dispatch",
        action="store_true",
        help="exit 1 when a dispatch is unregistered, mismatched, or unreadable",
    )
    arguments = parser.parse_args(argv)
    lines = build_report(
        scenario_scans(),
        _load("operations.json", "operations"),
        (row["id"] for row in _load("rows.json", "rows")),
        asyncio.run(served_schemas()),
    )
    print("\n".join(lines))
    return exit_status(lines, fail_on_dispatch=arguments.fail_on_dispatch)


if __name__ == "__main__":
    sys.exit(main())
