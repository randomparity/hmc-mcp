"""Construct HMC clients from configured connection sources."""

from __future__ import annotations

from . import HMCClient
from ..config import build_config


def client_from_env(profile: str | None = None) -> HMCClient:
    """Create an HMC client from the selected profile or environment defaults."""
    return HMCClient(build_config(profile=profile))
