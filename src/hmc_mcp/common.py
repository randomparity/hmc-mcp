"""Shared helpers: build an HMCClient from env/CLI options."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from .client import HMCClient
from .config import HMCConfig


def client_from_env(**overrides) -> HMCClient:
    """Create an HMCClient, letting kwargs override env/.env settings."""
    config = HMCConfig(**{k: v for k, v in overrides.items() if v is not None})
    return HMCClient(config)


def run_with_client(
    client_factory: Callable[[], HMCClient],
    fn: Callable[[HMCClient], Awaitable[Any]],
) -> Any:
    """Open an HMC client from *client_factory*, run async *fn*, return the result.

    The single implementation of the "open a client session, run a coroutine
    against it, close" seam shared by the MCP server tools
    (:func:`hmc_mcp._app.with_client`) and the CLI commands
    (:func:`hmc_mcp.cli_app._with_client`). *client_factory* is what varies —
    env-only config for the server, env-plus-CLI-flag overrides for the CLI.
    """
    async def _go() -> Any:
        async with client_factory() as hmc:
            return await fn(hmc)
    return asyncio.run(_go())
