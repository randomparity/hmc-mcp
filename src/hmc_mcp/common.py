"""Shared helpers: build an HMCClient from env/CLI options."""

from __future__ import annotations

from .client import HMCClient
from .config import HMCConfig


def client_from_env(**overrides) -> HMCClient:
    """Create an HMCClient, letting kwargs override env/.env settings."""
    config = HMCConfig(**{k: v for k, v in overrides.items() if v is not None})
    return HMCClient(config)
