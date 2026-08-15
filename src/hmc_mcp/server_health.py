"""MCP tool for curated fleet-health exceptions."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ._app import _READ_ONLY, _run
from .common import client_from_env
from .operations_health import fleet_health
from .tool_registry import tool_module

tool, register_tools = tool_module()


@tool(annotations=_READ_ONLY)
def hmc_fleet_health(profile: str | None = None) -> dict[str, Any]:
    """Return exception-only health across managed systems, partitions, and jobs.

    Args:
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def operation():
        async with client_from_env(profile) as hmc:
            return asdict(await fleet_health(hmc))

    return _run(operation)
