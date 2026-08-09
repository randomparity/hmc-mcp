"""Shared app state and plumbing for the hmc-mcp CLI.

Holds the root :class:`typer.Typer` (``app``), every sub-command group
(``systems_app``, ``lpars_app``, ...), the global option state
(``GlobalOpts`` / ``GLOBALS``), the shared output / run helpers used by the
command bodies, the ``serve`` command, and the cross-domain UUID helpers
(``_is_uuid`` / ``_resolve_uuid``).

The per-domain command modules (``cli_systems``, ``cli_lpars``, ...) import
the group and helpers they need from here and register their commands via
``@<group>.command(...)``. ``cli.py`` imports this module and every domain
module so the command tree is fully built on import.
"""

from __future__ import annotations

import asyncio
import json
import socket
from typing import Any, Awaitable, Callable, NoReturn

import typer
from rich.console import Console
from rich.table import Table

from .common import client_from_env
from .config import HMCConfig

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



class GlobalOpts:
    host: str | None = None
    user: str | None = None
    password: str | None = None
    verify_ssl: bool | None = None


GLOBALS = GlobalOpts()


@app.callback()
def main(
    host: str | None = typer.Option(None, "--host", envvar="HMC_HOST", help="HMC hostname or IP"),
    user: str | None = typer.Option(None, "--user", "-u", envvar="HMC_USER", help="HMC user"),
    password: str | None = typer.Option(
        None, "--password", "-p", envvar="HMC_PASSWORD", help="HMC password", hide_input=True
    ),
    verify_ssl: bool | None = typer.Option(
        None, "--verify-ssl/--no-verify-ssl", envvar="HMC_VERIFY_SSL", help="Verify the HMC TLS certificate"
    ),
) -> None:
    GLOBALS.host = host
    GLOBALS.user = user
    GLOBALS.password = password
    GLOBALS.verify_ssl = verify_ssl


def _client():
    return client_from_env(
        host=GLOBALS.host,
        user=GLOBALS.user,
        password=GLOBALS.password,
        verify_ssl=GLOBALS.verify_ssl,
    )


def _ssh_config() -> HMCConfig:
    """Build the SSH HMCConfig, honoring the global CLI options.

    None overrides are dropped so env vars / .env fill the rest — the same
    contract as ``client_from_env`` (explicit init args would otherwise
    shadow the environment).
    """
    overrides = {
        "host": GLOBALS.host,
        "user": GLOBALS.user,
        "password": GLOBALS.password,
        "verify_ssl": GLOBALS.verify_ssl,
    }
    return HMCConfig(**{k: v for k, v in overrides.items() if v is not None})


def _run(fn: Callable[[], Awaitable[Any]]) -> Any:
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


def _print_json(data: Any) -> None:
    console.print_json(json.dumps(data, default=str))


def _resource(entry: dict[str, Any]) -> dict[str, Any]:
    return entry.get("Resource") or {}


def _g(entry: dict[str, Any], *names: str, default: str = "-") -> str:
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
    else:
        _print_json(entries)


def _fail(exc: Exception) -> NoReturn:
    err_console.print(f"[red]Error:[/red] {exc}")
    raise typer.Exit(code=1)






@app.command()
def serve(
    http: bool = typer.Option(False, "--http", help="Serve over streamable HTTP instead of stdio"),
    host: str = typer.Option("127.0.0.1", "--host", help="HTTP listen host (with --http)"),
    port: int = typer.Option(8000, "--port", help="HTTP listen port (with --http)"),
    allow_remote: bool = typer.Option(
        False,
        "--allow-remote",
        help="Bind beyond loopback (with --http). UNSAFE: the HTTP server has no "
        "authentication; you must gate it with an authenticated reverse proxy.",
    ),
) -> None:
    """Run the MCP server (stdio by default — what agents expect).

    The HTTP transport is UNAUTHENTICATED and exposes the full tool surface,
    including arbitrary HMC CLI execution (``hmc_run_command``) and user
    administration. Bind only to loopback (the default). To reach the server
    beyond localhost you must pass ``--allow-remote`` AND put an authenticated
    reverse proxy (MCP gateway or HTTPS proxy with bearer-token auth) in front.
    """
    from . import server

    if http:
        if not _is_loopback(host) and not allow_remote:
            raise typer.BadParameter(
                f"--host {host!r} binds beyond loopback, but the streamable HTTP "
                "server has no authentication and exposes the full tool surface "
                "(incl. arbitrary HMC CLI exec and user admin). Refusing to start. "
                "If you understand the risk, re-run with --allow-remote and put an "
                "authenticated reverse proxy in front."
            )
        server.main_http(host=host, port=port)
    else:
        server.main_stdio()


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
        and (addr[4][0].startswith("127.") or addr[4][0] == "::1")
        for addr in infos
    )




def _is_uuid(value: str) -> bool:
    return "-" in value and len(value) == 36



async def _resolve_uuid(hmc, name_or_uuid: str) -> str | None:
    if _is_uuid(name_or_uuid):
        return name_or_uuid
    found = await hmc.find_partition_by_name(name_or_uuid)
    return str(found.get("UUID") or "") if found else None

