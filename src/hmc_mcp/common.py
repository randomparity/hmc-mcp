"""Shared helpers: build an HMCClient from env/CLI options, UUID predicates."""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Awaitable, Callable
from typing import Any

from .client import HMCClient
from .config import ConfigError, HMCConfig, load_profile, resolve_config_path

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


def client_from_env(profile: str | None = None, **overrides) -> HMCClient:
    """Create an HMCClient from environment variables, TOML profile, or explicit overrides.

    Resolution order (highest to lowest priority):
      1. Explicit *overrides* kwargs (CLI flags)
      2. ``HMC_*`` environment variables
      3. TOML profile (~/.config/hmc-mcp/config.toml or platform equivalent)

    When no explicit ``host`` is given and ``HMC_HOST`` is not set, the TOML
    profile loader is invoked with *profile* (or ``HMC_PROFILE`` env var or
    ``default_profile`` from the TOML file).  If no profile can be selected,
    falls back to plain ``HMCConfig()`` with env vars only.

    Checkout-local ``.env`` files are NOT loaded.
    """
    filtered = {k: v for k, v in overrides.items() if v is not None}

    # When no explicit host is given, try the TOML profile loader first
    explicit_host = filtered.get("host")
    if not explicit_host and not os.environ.get("HMC_HOST"):
        config_path = resolve_config_path()
        if config_path is not None or profile or os.environ.get("HMC_PROFILE"):
            try:
                base = load_profile(profile=profile)
                if filtered:
                    # Merge overrides on top of the loaded profile values
                    merged = {k: getattr(base, k) for k in base.model_fields}
                    merged.update(filtered)
                    base = HMCConfig(_env_file=None, **merged)  # type: ignore[call-arg]
                return HMCClient(base)
            except ConfigError:
                if profile:
                    # An explicit profile name was supplied but not found — raise
                    # so the caller gets a clear error rather than silently routing
                    # to the env-var default HMC.
                    raise
                pass  # No profile specified; fall through to env-var-only construction

    config = HMCConfig(_env_file=None, **filtered)  # type: ignore[call-arg]
    return HMCClient(config)


def run_with_client(
    client_factory: Callable[[], HMCClient],
    fn: Callable[[HMCClient], Awaitable[Any]],
) -> Any:
    """Open an HMC client from *client_factory*, run async *fn*, return the result.

    Shared by CLI commands (:func:`hmc_mcp.cli_app._with_client`).
    *client_factory* is what varies — env-only config for the server,
    env-plus-CLI-flag overrides for the CLI.
    """
    async def _go() -> Any:
        async with client_factory() as hmc:
            return await fn(hmc)
    return asyncio.run(_go())
