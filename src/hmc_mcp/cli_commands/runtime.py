"""Connection construction and async execution for CLI commands."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any, TypeVar

import typer
from typer._click.globals import get_current_context

from ..client import HMCClient
from ..config import HMCConfig, build_config
from .output import fail

_T = TypeVar("_T")


@dataclass(frozen=True)
class GlobalOpts:
    """Immutable connection options stored on one root CLI context."""

    host: str | None = None
    user: str | None = None
    password: str | None = None
    verify_ssl: bool | None = None
    profile: str | None = None
    command_line_options: frozenset[str] = frozenset()


def current_options() -> GlobalOpts:
    """Return the connection options belonging to the active CLI invocation."""
    ctx = get_current_context(silent=True)
    if ctx is None or not isinstance(ctx.find_root().obj, GlobalOpts):
        raise RuntimeError(
            "CLI connection options are unavailable outside an invocation"
        )
    return ctx.find_root().obj


def client() -> HMCClient:
    """Build a REST client from the active invocation's connection options."""
    options = current_options()
    return HMCClient(
        build_config(
            profile=options.profile,
            host=options.host,
            user=options.user,
            password=options.password,
            verify_ssl=options.verify_ssl,
        )
    )


def ssh_config() -> HMCConfig:
    """Build an SSH configuration from the active invocation's options."""
    options = current_options()
    return build_config(
        profile=options.profile,
        host=options.host,
        user=options.user,
        password=options.password,
        verify_ssl=options.verify_ssl,
    )


def run(fn: Callable[[], Coroutine[Any, Any, _T]]) -> _T:
    """Run a coroutine-returning closure through the CLI error path."""
    try:
        return asyncio.run(fn())
    except (typer.Abort, typer.Exit):
        raise
    except Exception as exc:
        fail(exc)


def with_client(fn: Callable[[HMCClient], Awaitable[_T]]) -> _T:
    """Run one async client call using the active connection options."""

    async def operation() -> _T:
        async with client() as hmc:
            return await fn(hmc)

    return run(operation)
