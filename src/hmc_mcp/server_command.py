"""Opt-in MCP escape hatch for arbitrary HMC CLI commands."""

from __future__ import annotations

from collections.abc import Callable

from fastmcp import FastMCP

from ._app import _run
from .common import build_config
from .ssh import run_hmc_cli
from .tool_registry import ToolSecurity, annotations_for, validate_security


def hmc_run_command(cmd: str, profile: str | None = None) -> str:
    """Execute an arbitrary HMC CLI command over SSH.

    This operator escape hatch can change HMC state. Prefer a dedicated tool
    when one exists. SSH authentication comes from the selected HMC profile.

    Args:
        cmd: Complete HMC CLI command to execute without shell mediation.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    config = build_config(profile=profile)
    return _run(lambda: run_hmc_cli(cmd, config))


HMC_RUN_COMMAND_SECURITY = ToolSecurity(
    effect="arbitrary-command",
    operation="command.run",
    target_kind="console",
)
validate_security(HMC_RUN_COMMAND_SECURITY, hmc_run_command)


async def configure_arbitrary_command_tool(
    enabled: bool,
    mcp: FastMCP,
    *,
    permits: Callable[[str], bool] | None = None,
) -> None:
    """Make escape-hatch registration match the requested capability state.

    The ``--enable-arbitrary-command`` flag is the outer gate and *permits* is
    the access policy's ceiling; per ADR 0036 they compose conjunctively, so the
    tool is registered only when both admit it. ``None`` means no ceiling.
    """
    permitted = enabled and (permits is None or permits("hmc_run_command"))
    registered = await mcp.local_provider.get_tool("hmc_run_command") is not None
    if permitted and not registered:
        mcp.tool(
            hmc_run_command,
            annotations=annotations_for(HMC_RUN_COMMAND_SECURITY.effect),
        )
    elif not permitted and registered:
        mcp.local_provider.remove_tool("hmc_run_command")
