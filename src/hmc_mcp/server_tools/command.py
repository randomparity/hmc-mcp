"""Opt-in MCP escape hatch for arbitrary HMC CLI commands."""

from __future__ import annotations

from collections.abc import Callable

from fastmcp import FastMCP

from .._app import run_sync
from ..config import build_config
from ..ssh import run_hmc_cli
from ..tool_registry import (
    Authorize,
    ToolSecurity,
    annotations_for,
    authorized,
    validate_security,
)


def hmc_run_command(cmd: str, profile: str | None = None) -> str:
    """Execute an arbitrary HMC CLI command over SSH.

    This operator escape hatch can change HMC state. Prefer a dedicated tool
    when one exists. SSH authentication comes from the selected HMC profile.

    Args:
        cmd: Complete HMC CLI command to execute without shell mediation.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    config = build_config(profile=profile)
    return run_sync(lambda: run_hmc_cli(cmd, config))


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
    permits: Callable[[str], bool],
    authorize: Authorize,
) -> None:
    """Make escape-hatch registration match the requested capability state.

    The ``--enable-arbitrary-command`` flag is the outer gate and *permits* is
    the access policy's ceiling; per ADR 0036 they compose conjunctively, so the
    tool is registered only when both admit it.

    *authorize* is the same dispatch-time gate the domain registration sites
    take, applied through the same helper — this is the site that registers the
    one ``arbitrary-command`` tool, so it is the one that least tolerates an
    exemption.

    Both gates are **required** since ADR 0041, and this is the only registration
    site where that matters as more than tidiness: it is the one that runs outside
    ``create_mcp``. While they defaulted to ``None``, calling this on an
    application composed from a read-only policy registered ``hmc_run_command``
    with no ceiling check and no authorizer at all — the fail-open ADR 0041 claims
    to have removed, surviving at the highest-risk tool in the package. ADR 0038
    left them optional on the cost of updating a dozen call sites; every
    non-test caller now passes both, so that cost is gone.
    """
    permitted = enabled and permits("hmc_run_command")
    registered = await mcp.local_provider.get_tool("hmc_run_command") is not None
    if permitted and not registered:
        mcp.tool(
            authorized(
                "hmc_run_command",
                HMC_RUN_COMMAND_SECURITY,
                hmc_run_command,
                authorize,
            ),
            annotations=annotations_for(HMC_RUN_COMMAND_SECURITY.effect),
        )
    elif not permitted and registered:
        mcp.local_provider.remove_tool("hmc_run_command")
