"""Regression coverage for CLI/MCP composition boundaries."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from hmc_mcp.cli import app


class _ClientContext:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, *_args):
        return None


def test_cli_import_does_not_register_mcp_tools():
    script = """
import asyncio
from hmc_mcp._app import create_mcp
before = create_mcp()
import hmc_mcp.cli_lpars
import hmc_mcp.cli_systems
after = create_mcp()
counts = (len(asyncio.run(before.list_tools())), len(asyncio.run(after.list_tools())))
raise SystemExit(0 if before is not after and counts == (0, 0) else 1)
"""
    subprocess.run([sys.executable, "-c", script], check=True)


def test_domain_import_does_not_register_tools_on_base_application():
    script = """
import asyncio
from hmc_mcp._app import create_mcp
application = create_mcp()
import hmc_mcp.server_lpars
raise SystemExit(0 if len(asyncio.run(application.list_tools())) == 0 else 1)
"""
    subprocess.run([sys.executable, "-c", script], check=True)


def test_create_mcp_returns_independent_complete_applications():
    import asyncio

    from hmc_mcp.server import create_mcp

    first = create_mcp()
    second = create_mcp()

    assert first is not second
    assert len(asyncio.run(first.list_tools())) == 111
    assert len(asyncio.run(second.list_tools())) == 111


def test_operations_do_not_import_application_modules():
    package = Path(__file__).parents[2] / "src" / "hmc_mcp"
    forbidden = {"_app", "server", "hmc_mcp._app", "hmc_mcp.server"}

    for path in package.glob("operations_*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        assert not imports & forbidden, path


def test_lpar_summary_cli_delegates_to_neutral_operation():
    client = object()
    summary = AsyncMock(return_value={"name": "aix1"})
    with (
        patch("hmc_mcp.operations_composite.lpar_summary", summary),
        patch("hmc_mcp.cli_lpars._client", return_value=_ClientContext(client)),
    ):
        result = CliRunner().invoke(app, ["lpars", "summary", "aix1", "--json"])
    assert result.exit_code == 0
    summary.assert_awaited_once_with(client, "aix1")


def test_system_summary_cli_delegates_to_neutral_operation():
    client = object()
    summary = AsyncMock(return_value={"name": "system1"})
    with (
        patch("hmc_mcp.operations_composite.system_summary", summary),
        patch("hmc_mcp.cli_systems._client", return_value=_ClientContext(client)),
    ):
        result = CliRunner().invoke(app, ["systems", "summary", "system1", "--json"])
    assert result.exit_code == 0
    summary.assert_awaited_once_with(client, "system1")


def test_capacity_clis_delegate_to_neutral_operations():
    client = object()
    report = AsyncMock(return_value=[])
    placement = AsyncMock(return_value=[])
    with (
        patch("hmc_mcp.operations_capacity.capacity_report", report),
        patch("hmc_mcp.operations_capacity.find_placement", placement),
        patch("hmc_mcp.cli_systems._client", return_value=_ClientContext(client)),
    ):
        capacity_result = CliRunner().invoke(app, ["systems", "capacity", "--json"])
        placement_result = CliRunner().invoke(
            app, ["systems", "find-placement", "4096", "--json"]
        )
    assert capacity_result.exit_code == 0
    assert placement_result.exit_code == 0
    report.assert_awaited_once_with(client)
    placement.assert_awaited_once_with(client, 4096, 0.5)


def test_capacity_cli_preserves_connection_overrides():
    client = object()
    report = AsyncMock(return_value=[])
    with (
        patch("hmc_mcp.operations_capacity.capacity_report", report),
        patch(
            "hmc_mcp.cli_app.client_from_env",
            return_value=_ClientContext(client),
        ) as client_factory,
    ):
        result = CliRunner().invoke(
            app,
            [
                "--host",
                "hmc.override",
                "--user",
                "operator",
                "--password",
                "test-password",
                "--no-verify-ssl",
                "--profile",
                "lab",
                "systems",
                "capacity",
                "--json",
            ],
        )

    assert result.exit_code == 0
    client_factory.assert_called_once_with(
        profile="lab",
        host="hmc.override",
        user="operator",
        password="test-password",  # pragma: allowlist secret
        verify_ssl=False,
    )
    report.assert_awaited_once_with(client)


def test_provision_cli_delegates_to_neutral_operation():
    client = object()
    from hmc_mcp.operations_provision import ProvisionResult

    provision = AsyncMock(
        return_value=ProvisionResult(False, False, None, True, None, (), ())
    )
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
    with (
        patch("hmc_mcp.operations_provision.provision_lpar", provision),
        patch("hmc_mcp.cli_lpars._client", return_value=_ClientContext(client)),
    ):
        result = CliRunner().invoke(app, args)
    assert result.exit_code == 0
    provision.assert_awaited_once()
    assert provision.await_args.args == (client,)
