"""Regression coverage for CLI/MCP composition boundaries."""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from hmc_mcp.cli import app


def test_cli_import_does_not_register_mcp_tools():
    script = """
import asyncio
from hmc_mcp._app import mcp
before = len(asyncio.run(mcp.list_tools()))
import hmc_mcp.cli_lpars
import hmc_mcp.cli_systems
after = len(asyncio.run(mcp.list_tools()))
raise SystemExit(0 if before == after == 0 else 1)
"""
    subprocess.run([sys.executable, "-c", script], check=True)


def test_lpar_summary_cli_delegates_to_neutral_operation():
    summary = AsyncMock(return_value={"name": "aix1"})
    with patch("hmc_mcp.operations_composite.lpar_summary", summary):
        result = CliRunner().invoke(app, ["lpars", "summary", "aix1", "--json"])
    assert result.exit_code == 0
    summary.assert_awaited_once_with("aix1")


def test_system_summary_cli_delegates_to_neutral_operation():
    summary = AsyncMock(return_value={"name": "system1"})
    with patch("hmc_mcp.operations_composite.system_summary", summary):
        result = CliRunner().invoke(
            app, ["systems", "summary", "system1", "--json"]
        )
    assert result.exit_code == 0
    summary.assert_awaited_once_with("system1")


def test_capacity_clis_delegate_to_neutral_operations():
    report = AsyncMock(return_value=[])
    placement = AsyncMock(return_value=[])
    with (
        patch("hmc_mcp.operations_capacity.capacity_report", report),
        patch("hmc_mcp.operations_capacity.find_placement", placement),
    ):
        capacity_result = CliRunner().invoke(
            app, ["systems", "capacity", "--json"]
        )
        placement_result = CliRunner().invoke(
            app, ["systems", "find-placement", "4096", "--json"]
        )
    assert capacity_result.exit_code == 0
    assert placement_result.exit_code == 0
    report.assert_awaited_once_with()
    placement.assert_awaited_once_with(4096, 0.5)


def test_provision_cli_delegates_to_neutral_operation():
    provision = AsyncMock(return_value={"created": False, "dry_run": True})
    args = [
        "lpars",
        "provision",
        "--system",
        "system1",
        "--name",
        "aix1",
        "--vlan",
        "100",
        "--vios-uuid",
        "vios-uuid",
        "--vios-partition-id",
        "2",
        "--vios-slot",
        "10",
        "--storage-name",
        "disk1",
        "--dry-run",
        "--json",
    ]
    with patch("hmc_mcp.operations_provision.provision_lpar", provision):
        result = CliRunner().invoke(app, args)
    assert result.exit_code == 0
    provision.assert_awaited_once()
