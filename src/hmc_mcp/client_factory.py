"""Construct HMC clients from configured connection sources."""

from __future__ import annotations

from typing import Any

from .client import HMCClient
from .config import build_config


def client_from_env(profile: str | None = None, **overrides: Any) -> HMCClient:
    """Create an HMC client from CLI options, environment, and TOML profiles."""
    return HMCClient(build_config(profile=profile, **overrides))
