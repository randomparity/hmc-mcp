"""Opt-in MCP escape hatch for arbitrary HMC CLI commands."""

from __future__ import annotations

from ._app import _STATE_CHANGING, _run, mcp
from .common import build_config
from .ssh import run_hmc_cli


def hmc_run_command(cmd: str, profile: str | None = None) -> str:
    """Execute an arbitrary HMC CLI command over SSH.

    This operator escape hatch can change HMC state. Prefer a dedicated tool
    when one exists. SSH authentication comes from the selected HMC profile.
    """
    config = build_config(profile=profile)
    return _run(lambda: run_hmc_cli(cmd, config))


async def register_arbitrary_command_tool() -> None:
    """Register the escape hatch once for a server that explicitly enables it."""
    if await mcp.local_provider.get_tool("hmc_run_command") is not None:
        return
    mcp.tool(hmc_run_command, annotations=_STATE_CHANGING)
