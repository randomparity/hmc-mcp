"""Regression coverage for CLI/MCP composition boundaries."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from hmc_mcp.cli import app
from hmc_mcp.operations.composite import _lpar_summary, _system_summary


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
import hmc_mcp.cli_commands.lpars_lifecycle
import hmc_mcp.cli_commands.systems
after = create_mcp()
counts = (len(asyncio.run(before.list_tools())), len(asyncio.run(after.list_tools())))
raise SystemExit(0 if before is not after and counts == (0, 0) else 1)
"""
    subprocess.run([sys.executable, "-c", script], check=True)


def test_cli_domain_import_does_not_mutate_shared_command_groups():
    script = """
from hmc_mcp.cli_commands.app import lpars_app
before = len(lpars_app.registered_commands)
import hmc_mcp.cli_commands.lpars_config
import hmc_mcp.cli_commands.lpars_create
import hmc_mcp.cli_commands.lpars_decommission
import hmc_mcp.cli_commands.lpars_lifecycle
import hmc_mcp.cli_commands.lpars_modify
after = len(lpars_app.registered_commands)
raise SystemExit(0 if before == after == 0 else 1)
"""
    subprocess.run([sys.executable, "-c", script], check=True)


def test_domain_import_does_not_register_tools_on_base_application():
    script = """
import asyncio
from hmc_mcp._app import create_mcp
application = create_mcp()
import hmc_mcp.server_tools.lpars
raise SystemExit(0 if len(asyncio.run(application.list_tools())) == 0 else 1)
"""
    subprocess.run([sys.executable, "-c", script], check=True)


def test_operation_modules_import_before_their_server_tool_consumers():
    """Keep operation modules independent of the application-facing tool layer."""
    script = """
import hmc_mcp.operations.lpar
import hmc_mcp.operations.systems
import hmc_mcp.operations.vios
import hmc_mcp.server_tools.lpars
import hmc_mcp.server_tools.systems
import hmc_mcp.server_tools.vios
"""
    subprocess.run([sys.executable, "-c", script], check=True)


def test_create_mcp_returns_independent_complete_applications():
    import asyncio

    from hmc_mcp.authorization.access_policy import DEFAULT_CONNECTION_TOKEN
    from hmc_mcp.cli_commands.legacy_policy import compile_legacy_policy
    from hmc_mcp.server import TOOL_SECURITY, create_mcp

    # ADR 0041 made the policy mandatory. The legacy-equivalent one registers exactly
    # the surface the no-argument call used to. ADR 0054 adds four read-only normalized
    # PCIe inventory tools. ADR 0055 replaces one unsafe assignment tool with
    # symmetric dedicated and SR-IOV assign/unassign tools. #375 adds the read-only
    # hmc_list_lpar_ownership and #385 the bounded hmc_capture_lpar_console
    # tool. Issue #399 replaces twelve unsupported user/password/LDAP tools
    # with nine documented UOM user/role/RemoteAccess tools. Issue #310 adds two
    # read-only LPAR memory-optimization score tools; #311 adds three read-only
    # affinity-planning tools; #312 adds two resource-group affinity tools; #314
    # adds three portable snapshot tools; #315 adds one minimum-affinity policy
    # read; #316 adds one guarded minimum-affinity policy write; #317 adds one
    # local read-only affinity assessment; #320 adds one affinity-aware LPM
    # operation; #362 removes hmc_detach_optical_mapping, which duplicated
    # hmc_unmount_optical_media, for 147 total. ADR 0103 splits VIOS updates and
    # upgrades into separate tools, for 148 total.
    policy = compile_legacy_policy(TOOL_SECURITY, (DEFAULT_CONNECTION_TOKEN,))

    first = create_mcp(policy)
    second = create_mcp(policy)

    assert first is not second
    assert len(asyncio.run(first.list_tools())) == 148
    assert len(asyncio.run(second.list_tools())) == 148


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


def test_tool_registry_does_not_import_the_policy_modules():
    """The dependency runs one way, which is why both gates travel as callables.

    ``access_policy`` imports ``tool_registry`` for its ``ToolSecurity`` index and
    ``connection_scope`` imports both, so an import in the other direction is a
    cycle — and the reason ``permits`` (ADR 0037) and ``authorize`` (ADR 0038) are
    parameters rather than the policy object.
    """
    package = Path(__file__).parents[2] / "src" / "hmc_mcp"
    forbidden = {
        "access_policy",
        "connection_scope",
        "hmc_mcp.authorization.access_policy",
        "hmc_mcp.authorization.connection_scope",
    }

    path = package / "tool_registry.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # `from . import access_policy` carries no module, which is the form
            # server.py uses for its own tool modules — collect both halves.
            if node.module is not None:
                modules.add(node.module)
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    assert not modules & forbidden, sorted(modules & forbidden)


def test_lpar_summary_cli_delegates_to_neutral_operation():
    client = object()
    summary = AsyncMock(
        return_value=_lpar_summary({"Resource": {"PartitionName": "aix1"}}, [])
    )
    with (
        patch("hmc_mcp.cli_commands.lpars_inventory.lpar_summary", summary),
        patch(
            "hmc_mcp.cli_commands.lpars_inventory._client",
            return_value=_ClientContext(client),
        ),
    ):
        result = CliRunner().invoke(app, ["lpars", "summary", "aix1", "--json"])
    assert result.exit_code == 0
    summary.assert_awaited_once_with(client, None, "aix1")


def test_system_summary_cli_delegates_to_neutral_operation():
    client = object()
    summary = AsyncMock(
        return_value=_system_summary({"Resource": {"SystemName": "system1"}}, [], [])
    )
    with (
        patch("hmc_mcp.cli_commands.systems.system_summary", summary),
        patch(
            "hmc_mcp.cli_commands.systems._client", return_value=_ClientContext(client)
        ),
    ):
        result = CliRunner().invoke(app, ["systems", "summary", "system1", "--json"])
    assert result.exit_code == 0
    summary.assert_awaited_once_with(client, "system1")


def test_fleet_health_cli_delegates_to_neutral_operation():
    from hmc_mcp.operations.health import FleetHealthResult

    client = object()
    health = AsyncMock(return_value=FleetHealthResult((), (), (), (), ()))
    with (
        patch("hmc_mcp.cli_commands.systems.fleet_health", health),
        patch(
            "hmc_mcp.cli_commands.systems._client", return_value=_ClientContext(client)
        ),
    ):
        result = CliRunner().invoke(app, ["systems", "health", "--json"])
    assert result.exit_code == 0
    health.assert_awaited_once_with(client)
    assert '"failed_jobs": []' in result.stdout


def test_fleet_health_cli_does_not_claim_healthy_when_telemetry_is_unavailable():
    from hmc_mcp.operations.health import FleetHealthResult

    client = object()
    warning = "Recent job health is unavailable"
    health = AsyncMock(return_value=FleetHealthResult((), (), (), (), (warning,)))
    with (
        patch("hmc_mcp.cli_commands.systems.fleet_health", health),
        patch(
            "hmc_mcp.cli_commands.systems._client", return_value=_ClientContext(client)
        ),
    ):
        result = CliRunner().invoke(app, ["systems", "health"])
    assert result.exit_code == 0
    assert "No fleet health exceptions found" not in result.stdout
    assert warning in result.stderr


def test_capacity_clis_delegate_to_neutral_operations():
    client = object()
    report = AsyncMock(return_value=[])
    placement = AsyncMock(return_value=[])
    with (
        patch("hmc_mcp.cli_commands.systems.capacity_report", report),
        patch("hmc_mcp.cli_commands.systems.find_placement", placement),
        patch(
            "hmc_mcp.cli_commands.systems._client", return_value=_ClientContext(client)
        ),
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
        patch("hmc_mcp.cli_commands.systems.capacity_report", report),
        patch(
            "hmc_mcp.cli_commands.app.HMCClient",
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
    client_factory.assert_called_once()
    config = client_factory.call_args.args[0]
    assert config.host == "hmc.override"
    assert config.user == "operator"
    assert config.password == "test-password"  # pragma: allowlist secret
    assert config.verify_ssl is False
    report.assert_awaited_once_with(client)


def test_provision_cli_delegates_to_neutral_operation():
    client = object()
    from hmc_mcp.operations.lpar.provision import ProvisionResult

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
        patch("hmc_mcp.cli_commands.lpars_provision.provision_lpar", provision),
        patch(
            "hmc_mcp.cli_commands.lpars_provision._client",
            return_value=_ClientContext(client),
        ),
    ):
        result = CliRunner().invoke(app, args)
    assert result.exit_code == 0
    provision.assert_awaited_once()
    assert provision.await_args.args == (client,)
