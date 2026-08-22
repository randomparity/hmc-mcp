"""Shared app state and plumbing for the hmc-mcp CLI.

Holds the root :class:`typer.Typer` (``app``), every sub-command group
(``systems_app``, ``lpars_app``, ...), the global option state
(``GlobalOpts``), the shared output / run helpers used by the
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
import logging
from dataclasses import dataclass
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, Final, NoReturn, TypeVar

import typer
from typer._click.globals import get_current_context
from typer._click.core import ParameterSource
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from .common import build_config, client_from_env, is_uuid, run_with_client
from .client import HMCClient
from .config import HMCConfig

_T = TypeVar("_T")

#: The level names ``--audit-level`` accepts. The audit record's own split —
#: permits at INFO, denials at WARNING (#224) — is what these select from.
_AUDIT_LEVELS: Final = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

app = typer.Typer(
    name="hmc-mcp",
    help="IBM HMC (Hardware Management Console) MCP server and CLI.",
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)

systems_app = typer.Typer(help="Managed systems (Power servers).", no_args_is_help=True)
lpars_app = typer.Typer(help="Logical partitions (LPARs).", no_args_is_help=True)
adapters_app = typer.Typer(
    help="Virtual adapters (network/storage) on LPARs.", no_args_is_help=True
)
storage_app = typer.Typer(
    help="VIOS storage: volume groups, virtual disks, mappings.", no_args_is_help=True
)
cluster_app = typer.Typer(
    help="Clusters / Shared Storage Pools (logical units).", no_args_is_help=True
)
metrics_app = typer.Typer(
    help="PCM performance/capacity metrics.", no_args_is_help=True
)
network_app = typer.Typer(
    help="Virtual networks / switches / bridges.", no_args_is_help=True
)
templates_app = typer.Typer(
    help="Template library (partition templates).", no_args_is_help=True
)
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

config_app = typer.Typer(
    # Not "profile configuration": this group writes two different files. Naming
    # only profiles here would assert the conflation the docs work to refuse, in
    # the surface an operator reaches straight from `serve`'s refusal.
    help="Connection profiles (config.toml) and server access policies "
    "(access-policy.toml).",
    no_args_is_help=True,
)
app.add_typer(config_app, name="config")


@dataclass(frozen=True)
class GlobalOpts:
    """Immutable snapshot of the CLI's global connection options.

    The Typer callback stores a fresh snapshot on the root context for each
    invocation. Frozen so no command can mutate shared state.
    """

    host: str | None = None
    user: str | None = None
    password: str | None = None
    verify_ssl: bool | None = None
    profile: str | None = None
    command_line_options: frozenset[str] = frozenset()


@app.callback()
def main(
    ctx: typer.Context,
    host: str | None = typer.Option(
        None, "--host", envvar="HMC_HOST", help="HMC hostname or IP"
    ),
    user: str | None = typer.Option(
        None, "--user", "-u", envvar="HMC_USER", help="HMC user"
    ),
    password: str | None = typer.Option(
        None,
        "--password",
        "-p",
        envvar="HMC_PASSWORD",
        help="HMC password",
        hide_input=True,
    ),
    verify_ssl: bool | None = typer.Option(
        None,
        "--verify-ssl/--no-verify-ssl",
        envvar="HMC_VERIFY_SSL",
        help="Verify the HMC TLS certificate",
    ),
    profile: str | None = typer.Option(
        None,
        "--profile",
        envvar="HMC_PROFILE",
        help="Named profile from ~/.config/hmc-mcp/config.toml (or platform equivalent)",
    ),
) -> None:
    option_names = ("host", "user", "password", "verify_ssl", "profile")
    command_line_options = frozenset(
        name
        for name in option_names
        if ctx.get_parameter_source(name) == ParameterSource.COMMANDLINE
    )
    ctx.obj = GlobalOpts(
        host=host,
        user=user,
        password=password,
        verify_ssl=verify_ssl,
        profile=profile,
        command_line_options=command_line_options,
    )


def _current_options() -> GlobalOpts:
    """Return the connection options belonging to the active CLI invocation."""
    ctx = get_current_context(silent=True)
    if ctx is None or not isinstance(ctx.find_root().obj, GlobalOpts):
        raise RuntimeError(
            "CLI connection options are unavailable outside an invocation"
        )
    return ctx.find_root().obj


def _client():
    options = _current_options()
    return client_from_env(
        profile=options.profile,
        host=options.host,
        user=options.user,
        password=options.password,
        verify_ssl=options.verify_ssl,
    )


def _ssh_config() -> HMCConfig:
    """Build the SSH HMCConfig, honoring the global CLI options.

    None overrides are dropped so env vars and the TOML profile fill the rest.
    When no explicit host is given, the TOML profile loader is tried first
    (same logic as ``client_from_env``).
    """
    options = _current_options()
    return build_config(
        profile=options.profile,
        host=options.host,
        user=options.user,
        password=options.password,
        verify_ssl=options.verify_ssl,
    )


def _run(fn: Callable[[], Coroutine[Any, Any, _T]]) -> _T:
    """Run a coroutine-returning closure, routing failures to the CLI error path.

    typer.Abort and typer.Exit are click's control-flow signals, not failures --
    both subclass RuntimeError, so they must be re-raised before the catch-all or
    "Aborted." is swallowed and a chosen exit code is rewritten to 1. Any other
    exception is reported via _fail and exits with code 1.
    """
    try:
        return asyncio.run(fn())
    except (typer.Abort, typer.Exit):
        raise
    except Exception as exc:
        _fail(exc)


def _with_client(fn: Callable[[HMCClient], Awaitable[_T]]) -> _T:
    """Run an async client call against the global connection options.

    Collapses the pervasive ``async def _go`` + ``X = _run(_go)`` idiom into
    one line for the common case where the body is a single client call.

    Control-flow signals pass through untouched, as in :func:`_run`.
    """
    try:
        return run_with_client(_client, fn)
    except (typer.Abort, typer.Exit):
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


def _output(
    entries: Any,
    as_json: bool,
    table: Table | None = None,
    empty_msg: str = "No results",
) -> None:
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
    err_console.print(f"[red]Error:[/red] {escape(str(exc))}")
    raise typer.Exit(code=1)


def _usage_error(message: str) -> NoReturn:
    """Report invalid command arguments using Typer's usage-error exit code."""
    err_console.print(f"[red]Error:[/red] {escape(message)}")
    raise typer.Exit(code=2)


def _partition_not_found(value: str) -> NoReturn:
    """Report a failed partition lookup consistently across CLI domains."""
    err_console.print(f"[yellow]Partition '{escape(value)}' not found[/yellow]")
    raise typer.Exit(code=1)


def _policy_file() -> tuple[str, bool] | None:
    """The access-policy path and whether it exists, or ``None`` if unresolvable.

    ``config_dir()`` reaches ``Path.home()``, which raises under a uid with no passwd
    entry and no ``HOME`` — a container or a systemd unit. A refusal that raises while
    rendering itself is worse than one that omits the path, so this returns ``None``
    there and the caller falls through to ``load_access_policy``, whose own guard
    reports it.

    ``tuple[str, bool] | None`` rather than ``str | None``: the predecessor this replaces
    collapsed *unresolvable* and *resolvable-but-absent* into one ``None``, and the
    absent case is precisely the one the refusal most needs the path for.
    """
    from .access_policy import resolve_access_policy_path

    try:
        path = resolve_access_policy_path()
        # `is_symlink()` as well as `exists()`: `exists()` follows the link, so a
        # dangling symlink — the natural way to point a container or unit at a mounted
        # policy, since `--access-policy` takes a NAME and not a path — reads as absent
        # here while the generator's O_EXCL create fails EEXIST on it. That pair would
        # tell the operator to run a generator that then tells them the file already
        # exists, with neither message mentioning a symlink. Reporting it as present
        # lets the load path speak instead, and its error names the resolved target.
        return str(path), path.exists() or path.is_symlink()
    except (RuntimeError, OSError, ValueError):
        return None


def _no_policy_selected(detail: str) -> str:
    """The migration text both refusals share.

    One text, because an operator meeting either has the same next step, and because
    nothing at this point distinguishes an upgrade from a first run — which is why it
    names the narrower documented examples as well as the generator. A message offering
    only the generator would send every fresh install to the widest policy expressible.
    """
    resolved = _policy_file()
    where = f" ({resolved[0]})" if resolved is not None else ""
    return (
        f"{detail}\n\n"
        "hmc-mcp will not serve without an access policy. To keep what an unpolicied "
        "server exposed, generate one and review it:\n"
        "    hmc-mcp config init-access-policy\n"
        f"then start the server with --access-policy legacy-equivalent{where}.\n"
        "For a new deployment, prefer one of the narrower examples in the README "
        "(read-only, or limited mutation) over the generated legacy-equivalent policy."
    )


@app.command()
def serve(
    http: bool = typer.Option(
        False, "--http", help="Serve over streamable HTTP instead of stdio"
    ),
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
    audit_level: str | None = typer.Option(
        None,
        "--audit-level",
        metavar="LEVEL",
        help="Minimum level for authorization audit records on stderr: permits "
        "are INFO, denials WARNING. DEBUG and INFO keep both; WARNING keeps "
        "denials only; ERROR or CRITICAL silences the stream.",
    ),
    access_policy: str | None = typer.Option(
        None,
        "--access-policy",
        metavar="NAME",
        help="REQUIRED. Enforce the named access policy from access-policy.toml: "
        "the server registers only the tools it permits, and refuses a call whose "
        "selected connection or target its grant does not name. Run "
        "'hmc-mcp config init-access-policy' to generate a policy matching what an "
        "unpolicied server exposed.",
    ),
) -> None:
    """Run the MCP server (stdio by default — what agents expect).

    The HTTP transport is UNAUTHENTICATED and exposes every enabled tool,
    including user administration and, with ``--enable-arbitrary-command``,
    arbitrary HMC CLI execution. Bind only to loopback (the default). To reach the server
    beyond localhost you must pass ``--allow-remote`` AND put an authenticated
    reverse proxy (MCP gateway or HTTPS proxy with bearer-token auth) in front.

    ``--access-policy NAME`` is **required**: the server refuses to start without
    one rather than serving unbounded. It registers only the tools that policy
    permits, and refuses any call whose selected HMC connection or target the
    granting entry does not name — so a grant's ``connections`` must hold profile
    keys, or ``<default>`` when ``HMC_HOST`` selects the connection from the
    environment.

    Run ``hmc-mcp config init-access-policy`` to generate a policy granting what an
    unpolicied server used to grant, review the file it writes, then pass its name
    here. Call ``hmc_effective_permissions`` to see what a running server exposes.
    """
    from . import server

    command_line_options = _current_options().command_line_options
    if command_line_options:
        options = ", ".join(
            f"--{name.replace('_', '-')}" for name in sorted(command_line_options)
        )
        raise typer.BadParameter(
            f"serve does not accept HMC connection options ({options}); "
            "configure the server with HMC_* environment variables or a configured HMC_PROFILE"
        )

    # Validated here rather than left to logging: a typo would otherwise surface
    # as a bare ValueError from Logger.setLevel partway through composition, after
    # the sink has begun installing, instead of a usage error — like every other
    # refusal above — that starts nothing.
    level: int | None = None
    if audit_level is not None:
        name = audit_level.upper()
        if name not in _AUDIT_LEVELS:
            raise typer.BadParameter(
                f"unknown --audit-level {audit_level!r}; use one of "
                + ", ".join(_AUDIT_LEVELS)
            )
        resolved = logging.getLevelName(name)
        assert isinstance(resolved, int), name  # guaranteed by _AUDIT_LEVELS
        level = resolved

    from .access_policy import AccessPolicyError, load_access_policy

    # A usage error: the invocation is incomplete. Checked after the HMC-option
    # rejection above, which is also exit 2 — that ordering is what
    # `test_serve_rejects_command_line_hmc_options` observes.
    if access_policy is None:
        _usage_error(_no_policy_selected("serve requires --access-policy NAME"))

    # Not a usage error: the command line was right and the environment was not.
    # Without this the likeliest upgrade order — edit the launcher, then discover the
    # file is missing — reaches `load_access_policy`'s unreadable-file arm and prints
    # "cannot be read: [Errno 2]", naming neither the generator nor the remedy. An
    # unresolvable path yields None and falls through to that guard instead.
    resolved = _policy_file()
    if resolved is not None and not resolved[1]:
        _fail(
            FileNotFoundError(
                _no_policy_selected(f"no access-policy file at {resolved[0]}")
            )
        )

    try:
        policy = load_access_policy(access_policy, server.TOOL_SECURITY)
    except AccessPolicyError as exc:
        _fail(exc)

    if http:
        try:
            server.main_http(
                policy,
                host=listen_host,
                port=port,
                enable_arbitrary_command=enable_arbitrary_command,
                allow_remote=allow_remote,
                audit_level=level,
            )
        except ValueError as exc:
            raise typer.BadParameter(
                f"{exc} Re-run with --allow-remote if you understand the risk."
            ) from exc
    else:
        server.main_stdio(
            policy,
            enable_arbitrary_command=enable_arbitrary_command,
            audit_level=level,
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
