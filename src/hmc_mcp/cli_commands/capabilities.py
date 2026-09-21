"""Local operation-maturity discovery for the CLI."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime
from typing import cast

import typer
from rich.table import Table

from hmc_mcp.operation_maturity import operation_maturity
from hmc_mcp.server_tools.catalog import TOOL_SECURITY
from hmc_mcp.tool_registry import ToolSecurity

from .output import console, print_json


def capability_rows(
    tool_security: Mapping[str, ToolSecurity], *, now: datetime | None = None
) -> list[dict[str, object]]:
    """Group tools by operation and project each operation's maturity once."""
    tools_by_operation: dict[str, list[str]] = defaultdict(list)
    for tool_name, security in tool_security.items():
        tools_by_operation[security.operation].append(tool_name)

    rows: list[dict[str, object]] = []
    for operation in sorted(tools_by_operation):
        maturity = operation_maturity(operation, now=now)
        row: dict[str, object] = {
            "operation": operation,
            "tools": sorted(tools_by_operation[operation]),
            "implementation": maturity.implementation,
            "verification": maturity.verification,
            "runtime_eligibility": maturity.runtime_eligibility,
        }
        if maturity.reason is not None:
            row["reason"] = maturity.reason
        rows.append(row)
    return rows


def capabilities(
    as_json: bool = typer.Option(False, "--json", help="Output JSON"),
) -> None:
    """Show the packaged operation-maturity projection for registered tools."""
    rows = capability_rows(TOOL_SECURITY)
    if as_json:
        print_json(rows)
        return

    table = Table()
    table.add_column("Operation")
    table.add_column("Tools")
    table.add_column("Implementation")
    table.add_column("Verification")
    table.add_column("Runtime eligibility")
    for row in rows:
        table.add_row(
            str(row["operation"]),
            ", ".join(cast(list[str], row["tools"])),
            str(row["implementation"]),
            str(row["verification"]),
            str(row["runtime_eligibility"]),
        )
    console.print(table)
