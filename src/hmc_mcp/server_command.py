"""Opt-in MCP escape hatch for arbitrary HMC CLI commands."""

from __future__ import annotations

from fastmcp import FastMCP

from ._app import _STATE_CHANGING, _run
from .common import build_config
from .ssh import run_hmc_cli


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


async def configure_arbitrary_command_tool(enabled: bool, mcp: FastMCP) -> None:
    """Make escape-hatch registration match the requested capability state."""
    registered = await mcp.local_provider.get_tool("hmc_run_command") is not None
    if enabled and not registered:
        mcp.tool(hmc_run_command, annotations=_STATE_CHANGING)
    elif not enabled and registered:
        mcp.local_provider.remove_tool("hmc_run_command")
