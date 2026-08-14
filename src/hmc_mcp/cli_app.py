"""Shared app state and plumbing for the hmc-mcp CLI.

Holds the root :class:`typer.Typer` (``app``), every sub-command group
(``systems_app``, ``lpars_app``, ...), the global option state
(``GlobalOpts`` / ``GLOBALS``), the shared output / run helpers used by the
command bodies, the ``serve`` command, and the cross-domain name-or-uuid
resolver (``_resolve_partition_uuid``). The ``is_uuid`` predicate itself lives
in :mod:`hmc_mcp.common` so the server (``_app``) and CLI share one
definition.

The per-domain command modules (``cli_systems``, ``cli_lpars``, ...) import
the group and helpers they need from here and register their commands via
``@<group>.command(...)``. ``cli.py`` imports this module and every domain
module so the command tree is fully built on import.
"""

from __future__ import annotations

import asyncio
import json
import socket
from dataclasses import dataclass
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, NoReturn, TypeVar

import typer
from typer._click.core import ParameterSource
from rich.console import Console
from rich.table import Table

from .common import build_config, client_from_env, is_uuid, run_with_client
from .client import HMCClient
from .config import HMCConfig

_T = TypeVar("_T")

app = typer.Typer(
    name="hmc-mcp",
    help="IBM HMC (Hardware Management Console) MCP server and CLI.",
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)

systems_app = typer.Typer(help="Managed systems (Power servers).", no_args_is_help=True)
lpars_app = typer.Typer(help="Logical partitions (LPARs).", no_args_is_help=True)
adapters_app = typer.Typer(help="Virtual adapters (network/storage) on LPARs.", no_args_is_help=True)
storage_app = typer.Typer(help="VIOS storage: volume groups, virtual disks, mappings.", no_args_is_help=True)
cluster_app = typer.Typer(help="Clusters / Shared Storage Pools (logical units).", no_args_is_help=True)
metrics_app = typer.Typer(help="PCM performance/capacity metrics.", no_args_is_help=True)
network_app = typer.Typer(help="Virtual networks / switches / bridges.", no_args_is_help=True)
templates_app = typer.Typer(help="Template library (partition templates).", no_args_is_help=True)
vios_app = typer.Typer(help="Virtual I/O Servers.", no_args_is_help=True)
console_app = typer.Typer(help="The HMC itself.", no_args_is_help=True)
jobs_app = typer.Typer(help="HMC jobs.", no_args_is_help=True)
raw_app = typer.Typer(help="Raw REST escape hatch.", no_args_is_help=True)

app.add_typer(systems_app, name="systems")
app.add_typer(lpars_app, name="lpars")
app.add_typer(adapters_app, name="adapters")
app.add_typer(storage_app, name="storage")
app.add_typer(cluster_app, name="cluster")
app.add_typer(metrics_app, name="metrics")
app.add_typer(network_app, name="network")
app.add_typer(templates_app, name="templates")
app.add_typer(vios_app, name="vios")
app.add_typer(console_app, name="console")
app.add_typer(jobs_app, name="jobs")
app.add_typer(raw_app, name="raw")

memory_pools_app = typer.Typer(help="Shared memory pools.", no_args_is_help=True)
app.add_typer(memory_pools_app, name="memory-pools")

config_app = typer.Typer(help="Profile configuration commands.", no_args_is_help=True)
app.add_typer(config_app, name="config")


@dataclass(frozen=True)
class GlobalOpts:
    """Immutable snapshot of the CLI's global connection options.

    The typer callback *replaces* ``GLOBALS`` with a fresh snapshot per
    invocation; commands read it through ``_client`` / ``_ssh_config``. Frozen
    so no command can mutate shared state.
    """

    host: str | None = None
    user: str | None = None
    password: str | None = None
    verify_ssl: bool | None = None
    profile: str | None = None
    command_line_options: frozenset[str] = frozenset()


GLOBALS = GlobalOpts()


@app.callback()
def main(
    ctx: typer.Context,
    host: str | None = typer.Option(None, "--host", envvar="HMC_HOST", help="HMC hostname or IP"),
    user: str | None = typer.Option(None, "--user", "-u", envvar="HMC_USER", help="HMC user"),
    password: str | None = typer.Option(
        None, "--password", "-p", envvar="HMC_PASSWORD", help="HMC password", hide_input=True
    ),
    verify_ssl: bool | None = typer.Option(
        None, "--verify-ssl/--no-verify-ssl", envvar="HMC_VERIFY_SSL", help="Verify the HMC TLS certificate"
    ),
    profile: str | None = typer.Option(
        None, "--profile", envvar="HMC_PROFILE",
        help="Named profile from ~/.config/hmc-mcp/config.toml (or platform equivalent)",
    ),
) -> None:
    global GLOBALS
    option_names = ("host", "user", "password", "verify_ssl", "profile")
    command_line_options = frozenset(
        name
        for name in option_names
        if ctx.get_parameter_source(name) == ParameterSource.COMMANDLINE
    )
    GLOBALS = GlobalOpts(
        host=host,
        user=user,
        password=password,
        verify_ssl=verify_ssl,
        profile=profile,
        command_line_options=command_line_options,
    )


def _client():
    return client_from_env(
        profile=GLOBALS.profile,
        host=GLOBALS.host,
        user=GLOBALS.user,
        password=GLOBALS.password,
        verify_ssl=GLOBALS.verify_ssl,
    )


def _ssh_config() -> HMCConfig:
    """Build the SSH HMCConfig, honoring the global CLI options.

    None overrides are dropped so env vars and the TOML profile fill the rest.
    When no explicit host is given, the TOML profile loader is tried first
    (same logic as ``client_from_env``).
    """
    return build_config(
        profile=GLOBALS.profile,
        host=GLOBALS.host,
        user=GLOBALS.user,
        password=GLOBALS.password,
        verify_ssl=GLOBALS.verify_ssl,
    )


def _run(fn: Callable[[], Coroutine[Any, Any, _T]]) -> _T:
    """Run a coroutine-returning closure, routing failures to the CLI error path.

    typer.Abort propagates so typer renders its own "Aborted." message;
    any other exception is reported via _fail and exits with code 1.
    """
    try:
        return asyncio.run(fn())
    except typer.Abort:
        raise
    except Exception as exc:
        _fail(exc)


def _with_client(fn: Callable[[HMCClient], Awaitable[_T]]) -> _T:
    """Run an async client call against the global connection options.

    Collapses the pervasive ``async def _go`` + ``X = _run(_go)`` idiom into
    one line for the common case where the body is a single client call.
    """
    try:
        return run_with_client(_client, fn)
    except typer.Abort:
        raise
    except Exception as exc:
        _fail(exc)


def _print_json(data: Any) -> None:
    console.print_json(json.dumps(data, default=str))


def _resource(entry: dict[str, Any]) -> dict[str, Any]:
    return entry.get("Resource") or {}


def _first_field(entry: dict[str, Any], *names: str, default: str = "-") -> str:
    """Get the first present key from an entry's Resource dict as a string."""
    res = _resource(entry)
    for name in names:
        value = res.get(name)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, dict) and "text" in value:
            return str(value["text"])
    return default


def _output(entries: Any, as_json: bool, table: Table | None = None, empty_msg: str = "No results") -> None:
    if as_json:
        _print_json(entries)
        return
    if table is not None:
        rows = table.row_count
        if rows == 0:
            err_console.print(f"[yellow]{empty_msg}[/yellow]")
        else:
            console.print(table)
    elif not entries:
        err_console.print(f"[yellow]{empty_msg}[/yellow]")
    else:
        _print_json(entries)


def _fail(exc: Exception) -> NoReturn:
    err_console.print(f"[red]Error:[/red] {exc}")
    raise typer.Exit(code=1)


def _usage_error(message: str) -> NoReturn:
    """Report invalid command arguments using Typer's usage-error exit code."""
    err_console.print(f"[red]Error:[/red] {message}")
    raise typer.Exit(code=2)


def _partition_not_found(value: str) -> NoReturn:
    """Report a failed partition lookup consistently across CLI domains."""
    err_console.print(f"[yellow]Partition '{value}' not found[/yellow]")
    raise typer.Exit(code=1)






@app.command()
def serve(
    http: bool = typer.Option(False, "--http", help="Serve over streamable HTTP instead of stdio"),
    listen_host: str = typer.Option(
        "127.0.0.1", "--listen-host", help="HTTP listen host (with --http)"
    ),
    port: int = typer.Option(8000, "--port", help="HTTP listen port (with --http)"),
    allow_remote: bool = typer.Option(
        False,
        "--allow-remote",
        help="Bind beyond loopback (with --http). UNSAFE: the HTTP server has no "
        "authentication; you must gate it with an authenticated reverse proxy.",
    ),
    enable_arbitrary_command: bool = typer.Option(
        False,
        "--enable-arbitrary-command",
        help="Expose hmc_run_command, which can execute any HMC CLI command.",
    ),
) -> None:
    """Run the MCP server (stdio by default — what agents expect).

    The HTTP transport is UNAUTHENTICATED and exposes every enabled tool,
    including user administration and, with ``--enable-arbitrary-command``,
    arbitrary HMC CLI execution. Bind only to loopback (the default). To reach the server
    beyond localhost you must pass ``--allow-remote`` AND put an authenticated
    reverse proxy (MCP gateway or HTTPS proxy with bearer-token auth) in front.
    """
    from . import server

    if GLOBALS.command_line_options:
        options = ", ".join(f"--{name.replace('_', '-')}" for name in sorted(GLOBALS.command_line_options))
        raise typer.BadParameter(
            f"serve does not accept HMC connection options ({options}); "
            "configure the server with HMC_* environment variables or a configured HMC_PROFILE"
        )

    if http:
        if not _is_loopback(listen_host) and not allow_remote:
            raise typer.BadParameter(
                f"--listen-host {listen_host!r} binds beyond loopback, but the streamable HTTP "
                "server has no authentication and exposes every enabled tool "
                "(including user administration). Refusing to start. "
                "If you understand the risk, re-run with --allow-remote and put an "
                "authenticated reverse proxy in front."
            )
        server.main_http(
            host=listen_host,
            port=port,
            enable_arbitrary_command=enable_arbitrary_command,
        )
    else:
        server.main_stdio(enable_arbitrary_command=enable_arbitrary_command)


def _is_loopback(host: str) -> bool:
    """True if host resolves to a loopback address (127.0.0.0/8 or ::1)."""
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    return any(
        addr[0] in (socket.AF_INET, socket.AF_INET6)
        and (str(addr[4][0]).startswith("127.") or addr[4][0] == "::1")
        for addr in infos
    )




async def _resolve_partition_uuid(hmc, name_or_uuid: str) -> str | None:
    """Resolve an LPAR name or UUID to its UUID.

    Partition-scoped: names are looked up via ``find_partition_by_name`` only.
    Returns None when *name_or_uuid* is neither a UUID nor a known partition
    name — callers decide how to report the miss.
    """
    if is_uuid(name_or_uuid):
        return name_or_uuid
    found = await hmc.find_partition_by_name(name_or_uuid)
    if not found or not found.get("UUID"):
        return None
    return str(found["UUID"])
