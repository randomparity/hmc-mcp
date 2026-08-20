"""Shared helpers: build an HMCClient from env/CLI options, UUID predicates."""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from .client import HMCClient
from .config import ConfigError, HMCConfig, load_profile, resolve_config_path

_T = TypeVar("_T")

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


def build_config(profile: str | None = None, **overrides: Any) -> HMCConfig:
    """Build configuration from environment, a TOML profile, and overrides.

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
                    # Preserve which profile fields were actually supplied. In
                    # particular, an omitted port must remain distinguishable
                    # from an explicit port equal to the default.
                    merged = {
                        key: getattr(base, key) for key in base.model_fields_set
                    }
                    merged.update(filtered)
                    base = HMCConfig(
                        _env_file=None,  # ty: ignore[unknown-argument]
                        **merged,
                    )
                return base
            except ConfigError:
                if profile:
                    # An explicit profile name was supplied but not found — raise
                    # so the caller gets a clear error rather than silently routing
                    # to the env-var default HMC.
                    raise
                pass  # No profile specified; fall through to env-var-only construction

    return HMCConfig(
        _env_file=None,  # ty: ignore[unknown-argument]
        **filtered,
    )


def client_from_env(profile: str | None = None, **overrides: Any) -> HMCClient:
    """Create an HMCClient using :func:`build_config` resolution."""
    return HMCClient(build_config(profile=profile, **overrides))


async def resolve_system_uuid(hmc: HMCClient, value: str) -> str:
    """Resolve a managed-system name or pass through its UUID."""
    if is_uuid(value):
        return value
    entry = await hmc.find_system_by_name(value)
    if not entry or not entry.get("UUID"):
        raise ValueError(
            f"No managed system named {value!r} found. "
            "Use hmc_list_systems to list available systems."
        )
    return str(entry["UUID"])


async def resolve_system_name(hmc: HMCClient, value: str) -> str:
    """Pass through a system name or resolve a UUID to its SystemName."""
    if not is_uuid(value):
        return value
    entry = await hmc.get_managed_system(value)
    name = ((entry or {}).get("Resource") or {}).get("SystemName")
    if not name:
        raise ValueError(
            f"Managed system {value!r} has no SystemName. "
            "Use hmc_list_systems to inspect available systems."
        )
    return str(name)


async def resolve_lpar_uuid(
    hmc: HMCClient, value: str, *, system_name_or_uuid: str | None = None
) -> str:
    """Resolve an LPAR name or pass through its UUID."""
    if is_uuid(value):
        return value
    system_uuid = (
        await resolve_system_uuid(hmc, system_name_or_uuid)
        if system_name_or_uuid is not None
        else None
    )
    entry = (
        await hmc.find_partition_by_name(value, system_uuid=system_uuid)
        if system_uuid is not None
        else await hmc.find_partition_by_name(value)
    )
    if not entry or not entry.get("UUID"):
        raise ValueError(
            f"No LPAR named {value!r} found. "
            "Use hmc_list_lpars to list available partitions."
        )
    return str(entry["UUID"])


async def resolve_vios_uuid(
    hmc: HMCClient, value: str, *, system_name_or_uuid: str | None = None
) -> str:
    """Resolve a VIOS name or pass through its UUID."""
    if is_uuid(value):
        return value
    system_uuid = (
        await resolve_system_uuid(hmc, system_name_or_uuid)
        if system_name_or_uuid is not None
        else None
    )
    entry = (
        await hmc.find_vios_by_name(value, system_uuid=system_uuid)
        if system_uuid is not None
        else await hmc.find_vios_by_name(value)
    )
    if not entry or not entry.get("UUID"):
        raise ValueError(
            f"No VIOS named {value!r} found. "
            "Use hmc_list_vios to list available Virtual I/O Servers."
        )
    return str(entry["UUID"])


def run_with_client(
    client_factory: Callable[[], HMCClient],
    fn: Callable[[HMCClient], Awaitable[_T]],
) -> _T:
    """Open an HMC client from *client_factory*, run async *fn*, return the result.

    Shared by CLI commands (:func:`hmc_mcp.cli_app._with_client`).
    *client_factory* is what varies — env-only config for the server,
    env-plus-CLI-flag overrides for the CLI.
    """

    async def _go() -> _T:
        async with client_factory() as hmc:
            return await fn(hmc)

    return asyncio.run(_go())
