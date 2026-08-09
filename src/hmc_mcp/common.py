"""Shared helpers: build an HMCClient from env/CLI options, UUID predicates."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any

from .client import HMCClient
from .config import HMCConfig

# Canonical UUID shape: 8-4-4-4-12 hex groups. Any 36-char dash-containing
# string is NOT a UUID (system/partition names can collide with that shape), so
# the predicate must reject non-hex characters or name/uuid disambiguation
# silently misroutes them as UUIDs.
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def is_uuid(value: str) -> bool:
    """True if *value* is a canonical 8-4-4-4-12 hex UUID."""
    return _UUID_RE.fullmatch(value) is not None


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
