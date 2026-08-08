"""hmc-mcp command line interface.

Usage examples:
    hmc-mcp serve                     # run the MCP server over stdio
    hmc-mcp systems list              # list managed systems as a table
    hmc-mcp lpars list --json         # list LPARs as JSON
    hmc-mcp lpars show mylpar         # find an LPAR by name and show it
    hmc-mcp console info              # HMC version / connectivity check
"""

from __future__ import annotations

import asyncio
import json
import shlex
import socket
from typing import Any, Awaitable, Callable, NoReturn, Optional

import typer
from rich.console import Console
from rich.table import Table

from .common import client_from_env
from .config import HMCConfig
from .jobs import power_off_lpar_job, power_on_lpar_job
from .ssh import run_hmc_command
from .templates import LparResources, PARTITION_TYPES, build_lpar_document

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


# ---------------------------------------------------------------------- #
# Global options / plumbing
# ---------------------------------------------------------------------- #


class GlobalOpts:
    host: Optional[str] = None
    user: Optional[str] = None
    password: Optional[str] = None
    verify_ssl: Optional[bool] = None


GLOBALS = GlobalOpts()


@app.callback()
def main(
    host: Optional[str] = typer.Option(None, "--host", envvar="HMC_HOST", help="HMC hostname or IP"),
    user: Optional[str] = typer.Option(None, "--user", "-u", envvar="HMC_USER", help="HMC user"),
    password: Optional[str] = typer.Option(
        None, "--password", "-p", envvar="HMC_PASSWORD", help="HMC password", hide_input=True
    ),
    verify_ssl: Optional[bool] = typer.Option(
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


# ---------------------------------------------------------------------- #
# serve
# ---------------------------------------------------------------------- #


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


# ---------------------------------------------------------------------- #
# console
# ---------------------------------------------------------------------- #


@console_app.command("info")
def console_info(as_json: bool = typer.Option(False, "--json")) -> None:
    """Show HMC version and network info (connectivity check)."""

    async def _go():
        async with _client() as hmc:
            return await hmc.get_console_info()

    info = _run(_go)

    if info is None:
        err_console.print("[yellow]No ManagementConsole data returned[/yellow]")
        return
    if as_json:
        _print_json(info)
        return
    res = _resource(info)
    console.print(f"[bold]HMC[/bold] {info.get('link') or ''}")
    for key in ("VersionInfo", "ManagementConsoleName", "MachineTypeModelSerialNumber", "NetworkInfo"):
        if key in res:
            console.print(f"  {key}: {json.dumps(res[key], default=str)}")


# ---------------------------------------------------------------------- #
# systems
# ---------------------------------------------------------------------- #


@systems_app.command("list")
def systems_list(as_json: bool = typer.Option(False, "--json")) -> None:
    """List managed systems."""

    async def _go():
        async with _client() as hmc:
            return await hmc.list_managed_systems()

    systems = _run(_go)

    table = None
    if not as_json:
        table = Table(title="Managed Systems")
        for col in ("Name", "UUID", "State", "MTMS", "IP Address"):
            table.add_column(col)
        for s in systems:
            table.add_row(
                _g(s, "SystemName"),
                s.get("UUID") or "-",
                _g(s, "State"),
                _g(s, "MachineTypeModelSerialNumber", "MTMS"),
                _g(s, "IPAddress", "PrimaryIPAddress"),
            )
    _output(systems, as_json, table, "No managed systems found")


@systems_app.command("show")
def systems_show(uuid: str = typer.Argument(..., help="Managed system UUID"),
                 as_json: bool = typer.Option(False, "--json")) -> None:
    """Show full details of one managed system."""

    async def _go():
        async with _client() as hmc:
            return await hmc.get_managed_system(uuid)

    system = _run(_go)

    if system is None:
        err_console.print(f"[yellow]System {uuid} not found[/yellow]")
        raise typer.Exit(code=1)
    _print_json(system)


@systems_app.command("power-on")
def systems_power_on(
    uuid: str = typer.Argument(..., help="Managed system UUID"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Power on a managed system (submits a PowerOn job)."""
    if not yes and not typer.confirm(f"Really PowerOn system {uuid}?"):
        raise typer.Abort()

    async def _go():
        async with _client() as hmc:
            return await hmc.power_on_system(uuid)

    job = _run(_go)

    console.print(f"[green]Submitted PowerOn for {uuid}[/green]")
    _print_json(job)


@systems_app.command("power-off")
def systems_power_off(
    uuid: str = typer.Argument(..., help="Managed system UUID"),
    immediate: bool = typer.Option(False, "--immediate"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Power off a managed system (submits a PowerOff job)."""
    op = "Immediate PowerOff" if immediate else "PowerOff"
    if not yes and not typer.confirm(f"Really {op} system {uuid}?"):
        raise typer.Abort()

    async def _go():
        async with _client() as hmc:
            return await hmc.power_off_system(uuid, immediate=immediate)

    job = _run(_go)

    console.print(f"[green]Submitted {op} for {uuid}[/green]")
    _print_json(job)


# ---------------------------------------------------------------------- #
# lpars
# ---------------------------------------------------------------------- #


@lpars_app.command("list")
def lpars_list(
    system: Optional[str] = typer.Option(None, "--system", "-s", help="Restrict to this managed system UUID"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List logical partitions."""

    async def _go():
        async with _client() as hmc:
            return await hmc.list_logical_partitions(system)

    lpars = _run(_go)

    table = None
    if not as_json:
        table = Table(title="Logical Partitions")
        for col in ("Name", "ID", "UUID", "State", "Type", "OS", "RMC"):
            table.add_column(col)
        for lpar in lpars:
            table.add_row(
                _g(lpar, "PartitionName"),
                _g(lpar, "PartitionID"),
                lpar.get("UUID") or "-",
                _g(lpar, "PartitionState"),
                _g(lpar, "PartitionType"),
                _g(lpar, "OperatingSystemVersion", default="-"),
                _g(lpar, "ResourceMonitoringControlState", "RMCState"),
            )
    _output(lpars, as_json, table, "No logical partitions found")


@lpars_app.command("show")
def lpars_show(
    name_or_uuid: str = typer.Argument(..., help="Partition name or UUID"),
    as_json: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    """Show one LPAR, looked up by name (exact) or by UUID."""

    async def _go():
        async with _client() as hmc:
            if _is_uuid(name_or_uuid):
                return await hmc.get_logical_partition(name_or_uuid)
            return await hmc.find_partition_by_name(name_or_uuid)

    lpar = _run(_go)

    if lpar is None:
        err_console.print(f"[yellow]Partition '{name_or_uuid}' not found[/yellow]")
        raise typer.Exit(code=1)
    _print_json(lpar)


@lpars_app.command("state")
def lpars_state(name_or_uuid: str = typer.Argument(..., help="Partition name or UUID")) -> None:
    """Print just the current state of an LPAR."""

    async def _go():
        async with _client() as hmc:
            uuid = name_or_uuid
            if not _is_uuid(name_or_uuid):
                found = await hmc.find_partition_by_name(name_or_uuid)
                if found is None:
                    return None
                uuid = str(found.get("UUID") or "")
            return await hmc.get_quick_property("LogicalPartition", uuid, "PartitionState")

    state = _run(_go)

    if state is None:
        err_console.print(f"[yellow]Partition '{name_or_uuid}' not found[/yellow]")
        raise typer.Exit(code=1)
    console.print(state)


@lpars_app.command("power-on")
def lpars_power_on(
    name_or_uuid: str = typer.Argument(..., help="Partition name or UUID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Power on an LPAR (submits a PowerOn job)."""
    _power_lpar(name_or_uuid, on=True, yes=yes)


@lpars_app.command("power-off")
def lpars_power_off(
    name_or_uuid: str = typer.Argument(..., help="Partition name or UUID"),
    immediate: bool = typer.Option(False, "--immediate", help="Immediate power off (no graceful shutdown)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Power off an LPAR (submits a PowerOff job)."""
    _power_lpar(name_or_uuid, on=False, immediate=immediate, yes=yes)


# ---------------------------------------------------------------------- #
# lpars: Live Partition Mobility
# ---------------------------------------------------------------------- #


def _lpm_run(name_or_uuid: str, fn, action: str, target: Optional[str], yes: bool) -> None:
    """Shared resolve -> confirm -> run helper for LPM operations."""

    async def _go():
        async with _client() as hmc:
            uuid = await _resolve_uuid(hmc, name_or_uuid)
            if uuid is None:
                return None, None
            if not yes:
                dest = f" to '{target}'" if target else ""
                if not typer.confirm(f"Really {action} partition '{name_or_uuid}' ({uuid}){dest}?"):
                    raise typer.Abort()
            return uuid, await fn(hmc, uuid)

    uuid, job = _run(_go)

    if uuid is None:
        err_console.print(f"[yellow]Partition '{name_or_uuid}' not found[/yellow]")
        raise typer.Exit(code=1)
    console.print(f"[green]Submitted {action} for {uuid}[/green]")
    _print_json(job)


@lpars_app.command("migrate")
def lpars_migrate(
    name_or_uuid: str = typer.Argument(..., help="Partition name or UUID"),
    target: str = typer.Option(..., "--target", help="Target managed system name"),
    profile: Optional[str] = typer.Option(None, "--profile", help="Target profile name"),
    wait_time: Optional[int] = typer.Option(None, "--wait-time", help="Override operation wait time"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Live-migrate (LPM) an LPAR to another managed system."""

    async def _fn(hmc, uuid):
        return await hmc.lpar_migrate(uuid, target, profile, wait_time=wait_time)

    _lpm_run(name_or_uuid, _fn, "Migrate", target, yes)


@lpars_app.command("migrate-validate")
def lpars_migrate_validate(
    name_or_uuid: str = typer.Argument(..., help="Partition name or UUID"),
    target: str = typer.Option(..., "--target", help="Target managed system name"),
    profile: Optional[str] = typer.Option(None, "--profile", help="Target profile name"),
    wait_time: Optional[int] = typer.Option(None, "--wait-time"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Validate whether an LPM migration would succeed."""

    async def _fn(hmc, uuid):
        return await hmc.lpar_migrate_validate(uuid, target, profile, wait_time=wait_time)

    _lpm_run(name_or_uuid, _fn, "MigrateValidate", target, yes)


@lpars_app.command("migrate-abort")
def lpars_migrate_abort(
    name_or_uuid: str = typer.Argument(..., help="Partition name or UUID"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Abort an in-progress LPM migration."""

    async def _fn(hmc, uuid):
        return await hmc.lpar_migrate_abort(uuid)

    _lpm_run(name_or_uuid, _fn, "MigrateAbort", None, yes)


@lpars_app.command("migrate-recover")
def lpars_migrate_recover(
    name_or_uuid: str = typer.Argument(..., help="Partition name or UUID"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Recover an LPAR after a failed LPM migration."""

    async def _fn(hmc, uuid):
        return await hmc.lpar_migrate_recover(uuid)

    _lpm_run(name_or_uuid, _fn, "MigrateRecover", None, yes)


@lpars_app.command("remote-restart")
def lpars_remote_restart(
    name_or_uuid: str = typer.Argument(..., help="Partition name or UUID"),
    target: str = typer.Option(..., "--target", help="Target managed system name"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Remote-restart a failed LPAR on another managed system."""

    async def _fn(hmc, uuid):
        return await hmc.lpar_remote_restart(uuid, target)

    _lpm_run(name_or_uuid, _fn, "RemoteRestart", target, yes)


def _is_uuid(value: str) -> bool:
    return "-" in value and len(value) == 36


def _power_lpar(name_or_uuid: str, on: bool, immediate: bool = False, yes: bool = False) -> None:
    async def _go():
        async with _client() as hmc:
            uuid = name_or_uuid
            name = name_or_uuid
            if not _is_uuid(name_or_uuid):
                found = await hmc.find_partition_by_name(name_or_uuid)
                if found is None:
                    return None, None
                uuid = str(found.get("UUID") or "")
                name = _g(found, "PartitionName", default=name_or_uuid)
            if not yes:
                op = "PowerOn" if on else ("Immediate PowerOff" if immediate else "PowerOff")
                if not typer.confirm(f"Really submit {op} for partition '{name}' ({uuid})?"):
                    err_console.print("Aborted.")
                    raise typer.Abort()
            if on:
                job = await hmc.submit_job(
                    f"/rest/api/uom/LogicalPartition/{uuid}/do/PowerOn", power_on_lpar_job()
                )
            else:
                job = await hmc.submit_job(
                    f"/rest/api/uom/LogicalPartition/{uuid}/do/PowerOff",
                    power_off_lpar_job(immediate=immediate),
                )
            return uuid, job

    uuid, job = _run(_go)

    if uuid is None:
        err_console.print(f"[yellow]Partition '{name_or_uuid}' not found[/yellow]")
        raise typer.Exit(code=1)
    console.print(f"[green]Job submitted[/green] for {uuid}")
    _print_json(job)


# ---------------------------------------------------------------------- #
# lpars: create / modify / delete
# ---------------------------------------------------------------------- #


@lpars_app.command("create")
def lpars_create(
    name: str = typer.Argument(..., help="Name for the new partition"),
    system: str = typer.Option(..., "--system", "-s", help="Target managed system UUID"),
    partition_type: str = typer.Option("AIX/Linux", "--type", help=f"One of: {', '.join(PARTITION_TYPES)}"),
    partition_id: Optional[int] = typer.Option(None, "--id", help="Partition ID (auto-assigned if omitted)"),
    min_memory: int = typer.Option(256, "--min-mem", help="Minimum memory (MiB)"),
    memory: int = typer.Option(4096, "--mem", help="Desired memory (MiB)"),
    max_memory: int = typer.Option(8192, "--max-mem", help="Maximum memory (MiB)"),
    dedicated: bool = typer.Option(False, "--dedicated", help="Dedicated CPUs instead of shared"),
    min_procs: Optional[float] = typer.Option(None, "--min-procs", help="Min processing units / dedicated CPUs"),
    procs: Optional[float] = typer.Option(None, "--procs", help="Desired processing units / dedicated CPUs"),
    max_procs: Optional[float] = typer.Option(None, "--max-procs", help="Max processing units / dedicated CPUs"),
    min_vcpus: Optional[int] = typer.Option(None, "--min-vcpus", help="Min virtual processors (shared)"),
    vcpus: Optional[int] = typer.Option(1, "--vcpus", help="Desired virtual processors (shared)"),
    max_vcpus: Optional[int] = typer.Option(2, "--max-vcpus", help="Max virtual processors (shared)"),
    capped: bool = typer.Option(False, "--capped", help="Cap shared CPU (default uncapped)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Create a new LPAR on a managed system.

    Creates the partition powered off with a default profile; storage/network
    and boot settings are configured afterwards via the HMC.
    """
    if not yes:
        typer.confirm(
            f"Create LPAR '{name}' ({partition_type}, {memory} MiB) on system {system}?",
            abort=True,
        )
    xml = build_lpar_document(
        name=name,
        partition_type=partition_type,
        partition_id=partition_id,
        resources=LparResources(
            min_memory=min_memory,
            desired_memory=memory,
            max_memory=max_memory,
            dedicated=dedicated,
            min_procs=min_procs,
            desired_procs=procs,
            max_procs=max_procs,
            min_vcpus=min_vcpus,
            desired_vcpus=vcpus,
            max_vcpus=max_vcpus,
            uncapped=not capped,
        ),
    )

    async def _go():
        async with _client() as hmc:
            return await hmc.create_logical_partition(system, xml)

    created = _run(_go)

    console.print(f"[green]Created LPAR '{name}'[/green]")
    _print_json(created)


@lpars_app.command("modify")
def lpars_modify(
    name_or_uuid: str = typer.Argument(..., help="Partition name or UUID"),
    new_name: Optional[str] = typer.Option(None, "--name", help="Rename the partition"),
    min_memory: Optional[int] = typer.Option(None, "--min-mem", help="Minimum memory (MiB)"),
    memory: Optional[int] = typer.Option(None, "--mem", help="Desired memory (MiB)"),
    max_memory: Optional[int] = typer.Option(None, "--max-mem", help="Maximum memory (MiB)"),
    dedicated: bool = typer.Option(False, "--dedicated", help="Assign dedicated CPUs"),
    min_procs: Optional[float] = typer.Option(None, "--min-procs"),
    procs: Optional[float] = typer.Option(None, "--procs"),
    max_procs: Optional[float] = typer.Option(None, "--max-procs"),
    min_vcpus: Optional[int] = typer.Option(None, "--min-vcpus"),
    vcpus: Optional[int] = typer.Option(None, "--vcpus"),
    max_vcpus: Optional[int] = typer.Option(None, "--max-vcpus"),
    capped: bool = typer.Option(False, "--capped", help="Cap shared CPU"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Change an LPAR's name and/or resource assignment (memory / CPU).

    Only options you pass are changed. On a running partition these are
    dynamic (DLPAR) operations and need RMC up; otherwise they apply on next
    activation.
    """
    if all(v is None for v in (new_name, min_memory, memory, max_memory,
                               min_procs, procs, max_procs, min_vcpus, vcpus, max_vcpus)):
        err_console.print("[yellow]Nothing to change — pass at least one option[/yellow]")
        raise typer.Exit(code=2)

    async def _go():
        async with _client() as hmc:
            uuid = name_or_uuid
            if not _is_uuid(name_or_uuid):
                found = await hmc.find_partition_by_name(name_or_uuid)
                if found is None:
                    return None, None
                uuid = str(found.get("UUID") or "")
            if not yes:
                if not typer.confirm(f"Apply resource changes to '{name_or_uuid}' ({uuid})?"):
                    raise typer.Abort()
            xml = build_lpar_document(
                name=new_name,
                resources=LparResources(
                    min_memory=min_memory,
                    desired_memory=memory,
                    max_memory=max_memory,
                    dedicated=dedicated,
                    min_procs=min_procs,
                    desired_procs=procs,
                    max_procs=max_procs,
                    min_vcpus=min_vcpus,
                    desired_vcpus=vcpus,
                    max_vcpus=max_vcpus,
                    uncapped=not capped,
                ),
            )
            return uuid, await hmc.modify_logical_partition(uuid, xml)

    uuid, updated = _run(_go)

    if uuid is None:
        err_console.print(f"[yellow]Partition '{name_or_uuid}' not found[/yellow]")
        raise typer.Exit(code=1)
    console.print(f"[green]Modified LPAR {uuid}[/green]")
    _print_json(updated)


@lpars_app.command("delete")
def lpars_delete(
    name_or_uuid: str = typer.Argument(..., help="Partition name or UUID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete (destroy) an LPAR. It must be powered off first."""

    async def _go():
        async with _client() as hmc:
            uuid = name_or_uuid
            if not _is_uuid(name_or_uuid):
                found = await hmc.find_partition_by_name(name_or_uuid)
                if found is None:
                    return None
                uuid = str(found.get("UUID") or "")
            if not yes:
                if not typer.confirm(
                    f"Permanently DELETE partition '{name_or_uuid}' ({uuid})? This cannot be undone."
                ):
                    raise typer.Abort()
            await hmc.delete_logical_partition(uuid)
            return uuid

    uuid = _run(_go)

    if uuid is None:
        err_console.print(f"[yellow]Partition '{name_or_uuid}' not found[/yellow]")
        raise typer.Exit(code=1)
    console.print(f"[green]Deleted LPAR {uuid}[/green]")


# ---------------------------------------------------------------------- #
# lpars: description (SSH CLI path — no REST equivalent)
# ---------------------------------------------------------------------- #


@lpars_app.command("get-description")
def lpars_get_description(
    lpar_name: str = typer.Argument(..., help="LPAR name"),
    system_name: str = typer.Argument(..., help="Managed system name"),
) -> None:
    """Get the description field of an LPAR (HMC CLI via SSH)."""
    config = _ssh_config()
    result = _run(lambda: run_hmc_command(
    config,
    f"lssyscfg -r lpar -m {shlex.quote(system_name)} "
    f"--filter lpar_names={shlex.quote(lpar_name)} -F description",
    ))

    console.print(result.strip() or "(no description set)")


@lpars_app.command("set-description")
def lpars_set_description(
    lpar_name: str = typer.Argument(..., help="LPAR name"),
    system_name: str = typer.Argument(..., help="Managed system name"),
    description: str = typer.Argument(..., help="New description text"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Set the description field of an LPAR (HMC CLI via SSH)."""
    if not yes and not typer.confirm(
        f"Set description on '{lpar_name}' (system {system_name})?"
    ):
        raise typer.Abort()
    config = _ssh_config()
    payload = f"name={lpar_name},description={description}"
    result = _run(lambda: run_hmc_command(
    config,
    f"chsyscfg -r lpar -m {shlex.quote(system_name)} -i {shlex.quote(payload)}",
    ))

    console.print(f"[green]Description updated for '{lpar_name}'[/green]")
    if result.strip():
        console.print(result.strip())


@lpars_app.command("get-msp")
def lpars_get_msp(
    lpar_name: str = typer.Argument(..., help="LPAR name"),
    system_name: str = typer.Argument(..., help="Managed system name"),
) -> None:
    """Get the MSP (Migratable Service Partition) flag of an LPAR (HMC CLI via SSH)."""
    config = _ssh_config()
    result = _run(lambda: run_hmc_command(
    config,
    f"lssyscfg -r lpar -m {shlex.quote(system_name)} "
    f"--filter lpar_names={shlex.quote(lpar_name)} -F msp",
    ))

    enabled = result.strip() == "1"
    console.print("enabled" if enabled else "disabled")


@lpars_app.command("set-msp")
def lpars_set_msp(
    lpar_name: str = typer.Argument(..., help="LPAR name"),
    system_name: str = typer.Argument(..., help="Managed system name"),
    enabled: bool = typer.Argument(..., help="True to enable MSP, False to disable"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Set the MSP (Migratable Service Partition) flag of an LPAR (HMC CLI via SSH)."""
    if not yes and not typer.confirm(
        f"Set MSP={'1' if enabled else '0'} on '{lpar_name}' (system {system_name})?"
    ):
        raise typer.Abort()
    config = _ssh_config()
    value = "1" if enabled else "0"
    payload = f"name={lpar_name},msp={value}"
    result = _run(lambda: run_hmc_command(
    config,
    f"chsyscfg -r lpar -m {shlex.quote(system_name)} -i {shlex.quote(payload)}",
    ))

    console.print(f"[green]MSP updated for '{lpar_name}'[/green]")
    if result.strip():
        console.print(result.strip())


# ---------------------------------------------------------------------- #
# lpars: processor compatibility mode (SSH CLI path)
# ---------------------------------------------------------------------- #


@lpars_app.command("get-proc-compat-modes")
def lpars_get_proc_compat_modes(
    system_name: str = typer.Argument(..., help="Managed system name"),
) -> None:
    """Get processor compatibility modes supported by a managed system (HMC CLI via SSH)."""
    config = _ssh_config()
    result = _run(lambda: run_hmc_command(
    config,
    f"lssyscfg -r sys -m {shlex.quote(system_name)} -F lpar_proc_compat_modes",
    ))

    console.print(result.strip() or "(no modes returned)")


@lpars_app.command("get-proc-compat")
def lpars_get_proc_compat(
    lpar_name: str = typer.Argument(..., help="LPAR name"),
    system_name: str = typer.Argument(..., help="Managed system name"),
    as_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """Get the current and pending processor compatibility modes for an LPAR (HMC CLI via SSH)."""
    config = _ssh_config()
    result = _run(lambda: run_hmc_command(
    config,
    f"lssyscfg -r lpar -m {shlex.quote(system_name)} "
    f"--filter lpar_names={shlex.quote(lpar_name)} -F "
    f"pend_lpar_proc_compat_mode,curr_lpar_proc_compat_mode",
    ))

    raw = result.strip()
    parts = raw.split(",") if raw else []
    pend = parts[0].strip() if len(parts) > 0 else ""
    curr = parts[1].strip() if len(parts) > 1 else ""

    if as_json:
        _print_json({"pend": pend, "curr": curr})
    else:
        table = Table(title=f"Processor Compatibility Mode: {lpar_name}")
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="green")
        table.add_row("Pending Mode", pend or "-")
        table.add_row("Current Mode", curr or "-")
        console.print(table)


@lpars_app.command("set-proc-compat")
def lpars_set_proc_compat(
    lpar_name: str = typer.Argument(..., help="LPAR name"),
    system_name: str = typer.Argument(..., help="Managed system name"),
    mode: str = typer.Argument(..., help="Processor compatibility mode to set"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Set the processor compatibility mode of an LPAR (HMC CLI via SSH)."""
    if not yes and not typer.confirm(
        f"Set processor compatibility mode to '{mode}' on LPAR '{lpar_name}' (system {system_name})?"
    ):
        raise typer.Abort()
    config = _ssh_config()
    payload = f"name={lpar_name},lpar_proc_compat_mode={mode}"
    result = _run(lambda: run_hmc_command(
    config,
    f"chsyscfg -r lpar -m {shlex.quote(system_name)} -i {shlex.quote(payload)}",
    ))

    console.print(f"[green]Processor compatibility mode updated for '{lpar_name}'[/green]")
    if result.strip():
        console.print(result.strip())


# ---------------------------------------------------------------------- #
# adapters
# ---------------------------------------------------------------------- #

_ADAPTER_TYPES = "ClientNetworkAdapter | VirtualSCSIClientAdapter | VirtualFibreChannelClientAdapter | VirtualNICDedicated"


@adapters_app.command("list")
def adapters_list(
    lpar: str = typer.Argument(..., help="LPAR name or UUID"),
    adapter_type: str = typer.Option("ClientNetworkAdapter", "--type", "-t", help=_ADAPTER_TYPES),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List an LPAR's virtual adapters of a given type."""

    async def _go():
        async with _client() as hmc:
            uuid = await _resolve_uuid(hmc, lpar)
            if uuid is None:
                return None, None
            return uuid, await hmc.list_child("LogicalPartition", uuid, adapter_type)

    uuid, adapters = _run(_go)

    if uuid is None:
        err_console.print(f"[yellow]Partition '{lpar}' not found[/yellow]")
        raise typer.Exit(code=1)
    _output(adapters, as_json, None, f"No {adapter_type} adapters on {lpar}")


@adapters_app.command("add-network")
def adapters_add_network(
    lpar: str = typer.Argument(..., help="LPAR name or UUID"),
    vlan: int = typer.Option(..., "--vlan", help="Port VLAN ID (PVID)"),
    slot: Optional[int] = typer.Option(None, "--slot", help="Virtual slot (auto if omitted)"),
    vswitch: Optional[int] = typer.Option(None, "--vswitch", help="VirtualSwitch ID"),
    tagged: bool = typer.Option(False, "--tagged", help="VLAN-tagged (trunking) adapter"),
    mac: Optional[str] = typer.Option(None, "--mac", help="Pin the MAC address"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Add a Virtual Ethernet (network) adapter to an LPAR."""

    async def _go():
        async with _client() as hmc:
            uuid = await _resolve_uuid(hmc, lpar)
            if uuid is None:
                return None, None
            if not yes and not typer.confirm(f"Add network adapter (VLAN {vlan}) to '{lpar}' ({uuid})?"):
                raise typer.Abort()
            return uuid, await hmc.add_network_adapter(uuid, vlan, slot, vswitch, tagged, mac)

    _adapter_mutation(_go, lpar, "network")


@adapters_app.command("add-vscsi")
def adapters_add_vscsi(
    lpar: str = typer.Argument(..., help="LPAR name or UUID"),
    vios_id: int = typer.Option(..., "--vios-id", help="VIOS PartitionID (integer)"),
    vios_slot: int = typer.Option(..., "--vios-slot", help="VIOS server-side slot"),
    slot: Optional[int] = typer.Option(None, "--slot", help="Client virtual slot (auto if omitted)"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Add a Virtual SCSI client adapter, paired to a VIOS."""

    async def _go():
        async with _client() as hmc:
            uuid = await _resolve_uuid(hmc, lpar)
            if uuid is None:
                return None, None
            if not yes and not typer.confirm(f"Add vSCSI adapter to '{lpar}' ({uuid}) via VIOS {vios_id}?"):
                raise typer.Abort()
            return uuid, await hmc.add_vscsi_adapter(uuid, vios_id, vios_slot, slot)

    _adapter_mutation(_go, lpar, "vSCSI")


@adapters_app.command("add-vfc")
def adapters_add_vfc(
    lpar: str = typer.Argument(..., help="LPAR name or UUID"),
    vios_id: int = typer.Option(..., "--vios-id", help="VIOS PartitionID (integer)"),
    vios_slot: int = typer.Option(..., "--vios-slot", help="VIOS server-side FC slot"),
    slot: Optional[int] = typer.Option(None, "--slot", help="Client virtual slot (auto if omitted)"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Add a Virtual Fibre Channel (NPIV) client adapter, paired to a VIOS."""

    async def _go():
        async with _client() as hmc:
            uuid = await _resolve_uuid(hmc, lpar)
            if uuid is None:
                return None, None
            if not yes and not typer.confirm(f"Add vFC adapter to '{lpar}' ({uuid}) via VIOS {vios_id}?"):
                raise typer.Abort()
            return uuid, await hmc.add_vfc_adapter(uuid, vios_id, vios_slot, slot)

    _adapter_mutation(_go, lpar, "vFC")


@adapters_app.command("delete")
def adapters_delete(
    lpar: str = typer.Argument(..., help="LPAR name or UUID"),
    adapter_type: str = typer.Option(..., "--type", "-t", help=_ADAPTER_TYPES),
    adapter_uuid: str = typer.Option(..., "--uuid", help="Adapter UUID (from `adapters list`)"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Remove a virtual adapter from an LPAR."""

    async def _go():
        async with _client() as hmc:
            uuid = await _resolve_uuid(hmc, lpar)
            if uuid is None:
                return None
            if not yes and not typer.confirm(f"Delete {adapter_type} {adapter_uuid} from '{lpar}'?"):
                raise typer.Abort()
            await hmc.delete_child("LogicalPartition", uuid, adapter_type, adapter_uuid)
            return uuid

    uuid = _run(_go)

    if uuid is None:
        err_console.print(f"[yellow]Partition '{lpar}' not found[/yellow]")
        raise typer.Exit(code=1)
    console.print(f"[green]Deleted {adapter_type} {adapter_uuid}[/green] from {uuid}")


async def _resolve_uuid(hmc, name_or_uuid: str) -> str | None:
    if _is_uuid(name_or_uuid):
        return name_or_uuid
    found = await hmc.find_partition_by_name(name_or_uuid)
    return str(found.get("UUID") or "") if found else None


def _adapter_mutation(go_coro, lpar: str, kind: str) -> None:
    uuid, result = _run(go_coro)
    if uuid is None:
        err_console.print(f"[yellow]Partition '{lpar}' not found[/yellow]")
        raise typer.Exit(code=1)
    console.print(f"[green]Added {kind} adapter[/green] to {uuid}")
    _print_json(result)


# ---------------------------------------------------------------------- #
# storage
# ---------------------------------------------------------------------- #


@storage_app.command("list-vgs")
def storage_list_vgs(
    vios: str = typer.Argument(..., help="VIOS UUID"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List Volume Groups on a VIOS (free space, PVs, virtual disks)."""

    async def _go():
        async with _client() as hmc:
            return await hmc.list_volume_groups(vios)

    vgs = _run(_go)

    table = None
    if not as_json:
        table = Table(title=f"Volume Groups on {vios}")
        for col in ("Name", "UUID", "Free (MiB)", "Capacity (MiB)"):
            table.add_column(col)
        for v in vgs:
            table.add_row(
                _g(v, "GroupName"),
                v.get("UUID") or "-",
                _g(v, "FreeSpace", "FreeSpaceInMBytes"),
                _g(v, "GroupCapacity", "Capacity"),
            )
    _output(vgs, as_json, table, "No volume groups found")


@storage_app.command("create-vg")
def storage_create_vg(
    vios: str = typer.Argument(..., help="VIOS UUID"),
    name: str = typer.Option(..., "--name", "-n", help="Volume Group name"),
    pvs: str = typer.Option(..., "--pvs", help="Comma-separated physical volumes, e.g. hdisk10,hdisk11"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Create a Volume Group on a VIOS from physical volumes."""
    pv_list = [p.strip() for p in pvs.split(",") if p.strip()]
    if not pv_list:
        err_console.print("[red]Provide at least one physical volume via --pvs[/red]")
        raise typer.Exit(code=2)
    if not yes and not typer.confirm(f"Create VG '{name}' from {pv_list} on VIOS {vios}?"):
        raise typer.Abort()

    async def _go():
        async with _client() as hmc:
            return await hmc.create_volume_group(vios, name, pv_list)

    vg = _run(_go)

    console.print(f"[green]Created Volume Group '{name}'[/green]")
    _print_json(vg)


@storage_app.command("create-disk")
def storage_create_disk(
    vios: str = typer.Argument(..., help="VIOS UUID"),
    vg: str = typer.Option(..., "--vg", help="Volume Group UUID"),
    name: str = typer.Option(..., "--name", "-n", help="Virtual disk name"),
    size: int = typer.Option(..., "--size", help="Size in MiB"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Create a Virtual Disk (logical volume) in a Volume Group."""
    if not yes and not typer.confirm(f"Create {size} MiB virtual disk '{name}' in VG {vg}?"):
        raise typer.Abort()

    async def _go():
        async with _client() as hmc:
            return await hmc.create_virtual_disk(vios, vg, name, size)

    disk = _run(_go)

    console.print(f"[green]Created virtual disk '{name}' ({size} MiB)[/green]")
    _print_json(disk)


@storage_app.command("map")
def storage_map(
    vios: str = typer.Argument(..., help="VIOS UUID"),
    lpar: str = typer.Option(..., "--lpar", help="Target LPAR name or UUID"),
    disk: str = typer.Option(..., "--disk", help="Storage name (DiskName or hdiskN)"),
    kind: str = typer.Option("VirtualDisk", "--kind", help="VirtualDisk or PhysicalVolume"),
    target: Optional[str] = typer.Option(None, "--target", help="Pin the vtscsi device name"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Map backing storage to an LPAR via a vSCSI mapping on a VIOS."""

    async def _go():
        async with _client() as hmc:
            lpar_uuid = await _resolve_uuid(hmc, lpar)
            if lpar_uuid is None:
                return None, None
            if not yes and not typer.confirm(
                f"Map {kind} '{disk}' on VIOS {vios} to LPAR '{lpar}' ({lpar_uuid})?"
            ):
                raise typer.Abort()
            return lpar_uuid, await hmc.map_storage_to_lpar(vios, kind, disk, lpar_uuid, target)

    lpar_uuid, result = _run(_go)

    if lpar_uuid is None:
        err_console.print(f"[yellow]Partition '{lpar}' not found[/yellow]")
        raise typer.Exit(code=1)
    console.print(f"[green]Mapped '{disk}'[/green] to {lpar_uuid}")
    _print_json(result)


# ---------------------------------------------------------------------- #
# cluster / SSP
# ---------------------------------------------------------------------- #


@cluster_app.command("list")
def cluster_list(
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List Clusters (VIOS node sets sharing a storage pool)."""

    async def _go():
        async with _client() as hmc:
            return await hmc.list_clusters()

    clusters = _run(_go)

    table = None
    if not as_json:
        table = Table(title="Clusters")
        for col in ("Name", "UUID"):
            table.add_column(col)
        for c in clusters:
            table.add_row(_g(c, "ClusterName"), c.get("UUID") or "-")
    _output(clusters, as_json, table, "No clusters found")


@cluster_app.command("list-ssps")
def cluster_list_ssps(
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List Shared Storage Pools (capacity, free space, logical units)."""

    async def _go():
        async with _client() as hmc:
            return await hmc.list_shared_storage_pools()

    ssps = _run(_go)

    table = None
    if not as_json:
        table = Table(title="Shared Storage Pools")
        for col in ("Name", "UUID", "Capacity (GB)", "Free (GB)"):
            table.add_column(col)
        for s in ssps:
            table.add_row(
                _g(s, "StoragePoolName"),
                s.get("UUID") or "-",
                _g(s, "Capacity"),
                _g(s, "FreeSpace"),
            )
    _output(ssps, as_json, table, "No shared storage pools found")


@cluster_app.command("create-lu")
def cluster_create_lu(
    cluster: str = typer.Argument(..., help="Cluster UUID"),
    name: str = typer.Option(..., "--name", "-n", help="Logical unit name"),
    size: int = typer.Option(..., "--size", help="Size in GB"),
    lu_type: str = typer.Option("THIN", "--type", help="THIN or THICK"),
    device_type: str = typer.Option("VirtualIO_Disk", "--device-type", help="VirtualIO_Disk or VirtualIO_Image"),
    cloned_from: Optional[str] = typer.Option(None, "--cloned-from", help="Source LU UDID to clone"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Create a Logical Unit in a Cluster/SSP (submits a job)."""
    if not yes and not typer.confirm(f"Create {size} GB {lu_type} LU '{name}' in cluster {cluster}?"):
        raise typer.Abort()

    async def _go():
        async with _client() as hmc:
            return await hmc.create_logical_unit(cluster, name, size, lu_type, device_type, cloned_from)

    job = _run(_go)

    console.print(f"[green]Submitted CreateLogicalUnit job for '{name}'[/green]")
    _print_json(job)


@cluster_app.command("delete-lu")
def cluster_delete_lu(
    cluster: str = typer.Argument(..., help="Cluster UUID"),
    udid: str = typer.Option(..., "--udid", help="Logical unit UDID to delete"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Delete a Logical Unit from a Cluster/SSP (submits a job)."""
    if not yes and not typer.confirm(f"Delete LU {udid} from cluster {cluster}? This is irreversible."):
        raise typer.Abort()

    async def _go():
        async with _client() as hmc:
            return await hmc.delete_logical_unit(cluster, udid)

    job = _run(_go)

    console.print(f"[green]Submitted DeleteLogicalUnit job for {udid}[/green]")
    _print_json(job)


# ---------------------------------------------------------------------- #
# metrics (PCM)
# ---------------------------------------------------------------------- #


@metrics_app.command("prefs")
def metrics_prefs(
    category: str = typer.Argument(..., help="e.g. ManagedSystem, LogicalPartition"),
    uuid: str = typer.Argument(..., help="Resource UUID"),
) -> None:
    """Show PCM monitoring preferences for a resource."""

    async def _go():
        async with _client() as hmc:
            return await hmc.get_pcm_preferences(category, uuid)

    prefs = _run(_go)

    _print_json(prefs)


@metrics_app.command("set-prefs")
def metrics_set_prefs(
    category: str = typer.Argument(..., help="e.g. ManagedSystem"),
    uuid: str = typer.Argument(..., help="Resource UUID"),
    ltm: Optional[bool] = typer.Option(None, "--ltm/--no-ltm", help="Long-term monitoring"),
    aggregation: Optional[bool] = typer.Option(None, "--aggregation/--no-aggregation"),
    stm: Optional[bool] = typer.Option(None, "--stm/--no-stm", help="Short-term monitoring"),
    energy: Optional[bool] = typer.Option(None, "--energy/--no-energy", help="Energy monitoring"),
) -> None:
    """Enable/disable PCM data collection for a resource."""
    flags: dict[str, bool] = {}
    if ltm is not None:
        flags["LongTermMonitorEnabled"] = ltm
    if aggregation is not None:
        flags["AggregationEnabled"] = aggregation
    if stm is not None:
        flags["ShortTermMonitorEnabled"] = stm
    if energy is not None:
        flags["EnergyMonitorEnabled"] = energy
    if not flags:
        err_console.print("[yellow]No flags supplied; nothing to change.[/yellow]")
        raise typer.Exit(code=2)

    async def _go():
        async with _client() as hmc:
            await hmc.set_pcm_preferences(category, uuid, **flags)
            return f"Updated {category} {uuid}: {flags}"

    msg = _run(_go)

    console.print(f"[green]{msg}[/green]")


@metrics_app.command("show")
def metrics_show(
    category: str = typer.Argument(..., help="e.g. ManagedSystem, LogicalPartition"),
    uuid: str = typer.Argument(..., help="Resource UUID"),
    start: str = typer.Option(..., "--start", help="Start TS yyyy-MM-ddTHH:mm:ssZ"),
    end: Optional[str] = typer.Option(None, "--end", help="End TS (optional)"),
    samples: Optional[int] = typer.Option(None, "--samples", help="Number of samples"),
    aggregated: bool = typer.Option(False, "--aggregated", help="Use aggregated (long-term) metrics"),
    fetch: bool = typer.Option(False, "--fetch", help="Also download the latest JSON doc"),
) -> None:
    """Get PCM metrics (processed by default; --aggregated for rollups)."""

    async def _go():
        async with _client() as hmc:
            fn = hmc.get_aggregated_metrics if aggregated else hmc.get_processed_metrics
            links = await fn(category, uuid, start, end, samples)
            if not fetch or not links:
                return links
            return await hmc.fetch_json(links[-1]["link"])

    result = _run(_go)

    _print_json(result)


# ---------------------------------------------------------------------- #
# storage: Virtual Media Repository / optical media
# ---------------------------------------------------------------------- #


@storage_app.command("create-media-repo")
def storage_create_media_repo(
    vios: str = typer.Argument(..., help="VIOS UUID"),
    vg: str = typer.Argument(..., help="Volume Group UUID"),
    size_mb: int = typer.Option(..., "--size-mb", help="Repository size in MB"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Create the Virtual Media Repository (VMLibrary) on a volume group."""
    if not yes and not typer.confirm(f"Create {size_mb} MB media repository on VG {vg} (VIOS {vios})?"):
        raise typer.Abort()

    async def _go():
        async with _client() as hmc:
            return await hmc.create_media_repository(vios, vg, size_mb)

    result = _run(_go)

    console.print(f"[green]Created media repository on {vg}[/green]")
    _print_json(result)


@storage_app.command("create-media")
def storage_create_media(
    vios: str = typer.Argument(..., help="VIOS UUID"),
    vg: str = typer.Argument(..., help="Volume Group UUID"),
    name: str = typer.Option(..., "--name", "-n", help="Media file name (e.g. aix.iso)"),
    size_mb: int = typer.Option(..., "--size-mb", help="Media size in MB"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Create a blank optical media (ISO container) in the media repository."""
    if not yes and not typer.confirm(f"Create media '{name}' ({size_mb} MB) on VG {vg} (VIOS {vios})?"):
        raise typer.Abort()

    async def _go():
        async with _client() as hmc:
            return await hmc.create_optical_media(vios, vg, name, size_mb)

    result = _run(_go)

    console.print(f"[green]Created media '{name}' on {vg}[/green]")
    _print_json(result)


@storage_app.command("delete-media-repo")
def storage_delete_media_repo(
    vios: str = typer.Argument(..., help="VIOS UUID"),
    vg: str = typer.Argument(..., help="Volume Group UUID"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Delete the Virtual Media Repository from a volume group."""
    if not yes and not typer.confirm(f"Delete media repository on VG {vg} (VIOS {vios})?"):
        raise typer.Abort()

    async def _go():
        async with _client() as hmc:
            return await hmc.delete_media_repository(vios, vg)

    _run(_go)

    console.print(f"[green]Deleted media repository on {vg}[/green]")


# ---------------------------------------------------------------------- #
# network (virtual switches / networks / bridges)
# ---------------------------------------------------------------------- #


@network_app.command("list-switches")
def network_list_switches(
    system: str = typer.Argument(..., help="Managed system UUID"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List VirtualSwitches on a managed system."""

    async def _go():
        async with _client() as hmc:
            return await hmc.list_virtual_switches(system)

    switches = _run(_go)

    table = None
    if not as_json:
        table = Table(title=f"Virtual Switches on {system}")
        for col in ("Name", "SwitchID", "Mode", "UUID"):
            table.add_column(col)
        for s in switches:
            table.add_row(
                _g(s, "SwitchName"),
                _g(s, "SwitchID"),
                _g(s, "SwitchMode"),
                s.get("UUID") or "-",
            )
    _output(switches, as_json, table, "No virtual switches found")


@network_app.command("list-networks")
def network_list_networks(
    system: str = typer.Argument(..., help="Managed system UUID"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List Virtual Networks (VLANs) on a managed system."""

    async def _go():
        async with _client() as hmc:
            return await hmc.list_virtual_networks(system)

    nets = _run(_go)

    table = None
    if not as_json:
        table = Table(title=f"Virtual Networks on {system}")
        for col in ("Name", "VLAN", "VswitchID", "Tagged", "UUID"):
            table.add_column(col)
        for n in nets:
            table.add_row(
                _g(n, "NetworkName"),
                _g(n, "NetworkVLANID"),
                _g(n, "VswitchID"),
                _g(n, "TaggedNetwork"),
                n.get("UUID") or "-",
            )
    _output(nets, as_json, table, "No virtual networks found")


@network_app.command("create")
def network_create(
    system: str = typer.Argument(..., help="Managed system UUID"),
    name: str = typer.Option(..., "--name", "-n", help="Network name"),
    vlan: int = typer.Option(..., "--vlan", help="VLAN ID"),
    vswitch: int = typer.Option(..., "--vswitch", help="Backing VirtualSwitch SwitchID"),
    tagged: bool = typer.Option(False, "--tagged", help="Tagged (bridged) network"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Create a Virtual Network (VLAN) on a managed system."""
    if not yes and not typer.confirm(f"Create network '{name}' (VLAN {vlan}, vswitch {vswitch}) on {system}?"):
        raise typer.Abort()

    async def _go():
        async with _client() as hmc:
            return await hmc.create_virtual_network(system, name, vlan, vswitch, tagged=tagged)

    net = _run(_go)

    console.print(f"[green]Created virtual network '{name}'[/green]")
    _print_json(net)


@network_app.command("delete")
def network_delete(
    system: str = typer.Argument(..., help="Managed system UUID"),
    uuid: str = typer.Option(..., "--uuid", help="Virtual Network UUID to delete"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Delete a Virtual Network from a managed system."""
    if not yes and not typer.confirm(f"Delete virtual network {uuid} from {system}?"):
        raise typer.Abort()

    async def _go():
        async with _client() as hmc:
            await hmc.delete_virtual_network(system, uuid)
            return uuid

    deleted = _run(_go)

    console.print(f"[green]Deleted virtual network {deleted}[/green]")


@network_app.command("list-bridges")
def network_list_bridges(
    system: str = typer.Argument(..., help="Managed system UUID"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List NetworkBridges (Shared Ethernet Adapters) on a managed system."""

    async def _go():
        async with _client() as hmc:
            return await hmc.list_network_bridges(system)

    bridges = _run(_go)

    _output(bridges, as_json, None, "No network bridges found")


@network_app.command("list-fc-ports")
def network_list_fc_ports(
    system: str = typer.Argument(..., help="Managed system name"),
    lpar_name: Optional[str] = typer.Option(None, "--lpar", help="Filter by LPAR name"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List Virtual Fibre Channel (NPIV) adapters on a managed system."""

    async def _go():
        from hmc_mcp.ssh import run_hmc_command
        import csv
        import io

        cmd = f"lshwres -r virtualio --rsubtype fc --level lpar -m {shlex.quote(system)}"
        if lpar_name:
            cmd += f" --filter lpar_names={shlex.quote(lpar_name)}"
        config = _ssh_config()
        raw = await run_hmc_command(config, cmd)
        if not raw.strip():
            return []
        reader = csv.DictReader(io.StringIO(raw.strip()))
        return [dict(row) for row in reader]

    ports = _run(_go)

    _output(ports, as_json, None, "No FC ports found")


@network_app.command("list-sea-adapters")
def network_list_sea_adapters(
    system: str = typer.Argument(..., help="Managed system name"),
    lpar_name: Optional[str] = typer.Option(None, "--lpar", help="Filter by LPAR name"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List Shared Ethernet Adapter (SEA) virtual Ethernet ports on a managed system."""

    async def _go():
        from hmc_mcp.ssh import run_hmc_command

        fields = "lpar_name,port_vlan_id,vswitch,state,trunk_priority"
        cmd = (
            f"lshwres -r virtualio --rsubtype eth --level lpar -m {shlex.quote(system)}"
            f" -F {fields}"
        )
        if lpar_name:
            cmd += f" --filter lpar_names={shlex.quote(lpar_name)}"
        config = _ssh_config()
        raw = await run_hmc_command(config, cmd)
        if not raw.strip():
            return []
        keys = fields.split(",")
        result = []
        for line in raw.strip().splitlines():
            values = line.split(",", len(keys) - 1)
            result.append(dict(zip(keys, values)))
        return result

    adapters = _run(_go)

    _output(adapters, as_json, None, "No SEA adapters found")


@network_app.command("set-sriov-mode")
def network_set_sriov_mode(
    system_name: str = typer.Argument(..., help="Managed system name"),
    adapter_id: str = typer.Argument(..., help="Physical adapter ID (from `hmc-mcp network list-io-slots`)"),
    mode: str = typer.Argument(..., help="'sriov' or 'dedicated'"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Toggle a physical SR-IOV adapter between SR-IOV and dedicated mode (HMC CLI via SSH)."""
    if mode not in {"sriov", "dedicated"}:
        err_console.print(f"[red]Invalid mode {mode!r}. Must be 'sriov' or 'dedicated'.[/red]")
        raise typer.Exit(code=2)
    if not yes and not typer.confirm(
        f"Set adapter {adapter_id} on system '{system_name}' to '{mode}' mode?"
    ):
        raise typer.Abort()
    config = _ssh_config()
    payload = f"sriov_adapter_mode={mode}"
    result = _run(lambda: run_hmc_command(
    config,
    f"chhwres -r sriov -m {shlex.quote(system_name)} -o s --id {shlex.quote(adapter_id)} "
    f"-a {shlex.quote(payload)}",
    ))

    console.print(f"[green]Adapter {adapter_id} set to '{mode}' mode on '{system_name}'[/green]")
    if result.strip():
        console.print(result.strip())


@network_app.command("list-vnics")
def network_list_vnics(
    system: str = typer.Argument(..., help="Managed system name"),
    lpar: str = typer.Argument(..., help="LPAR name"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List vNICs (SR-IOV-backed Virtual NICs) on an LPAR (HMC CLI via SSH)."""
    from .ssh import _parse_lshwres_output

    config = _ssh_config()
    raw = _run(lambda: run_hmc_command(
    config,
    f"lshwres -r virtualio --rsubtype vnic --level lpar -m {shlex.quote(system)} "
    f"--filter lpar_names={shlex.quote(lpar)}",
    ))

    vnics = _parse_lshwres_output(raw) if raw.strip() else []
    _output(vnics, as_json, None, "No vNICs found")


@network_app.command("add-vnic")
def network_add_vnic(
    system: str = typer.Argument(..., help="Managed system name"),
    lpar: str = typer.Argument(..., help="LPAR name"),
    capacity: int = typer.Option(..., "--capacity", "-c", help="vNIC capacity (1–100)"),
    vswitch: str = typer.Option(..., "--vswitch", help="Virtual switch name"),
    vlan: int = typer.Option(..., "--vlan", help="Port VLAN ID"),
    backing_devices: Optional[str] = typer.Option(None, "--backing-devices", help="Backing devices (opaque string, v1 only)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Add a vNIC to an LPAR (HMC CLI via SSH, v1 minimal parameters)."""
    if not yes and not typer.confirm(
        f"Add vNIC (capacity={capacity}, vswitch={vswitch}, vlan={vlan}) to '{lpar}' on '{system}'?"
    ):
        raise typer.Abort()

    attrs = f"capacity={capacity},vswitch_name={vswitch},port_vlan_id={vlan}"
    if backing_devices:
        attrs += f",backing_devices={backing_devices}"

    config = _ssh_config()
    result = _run(lambda: run_hmc_command(
    config,
    f"chhwres -r virtualio --rsubtype vnic -o a -m {shlex.quote(system)} "
    f"--filter lpar_names={shlex.quote(lpar)} "
    f"-a {shlex.quote(attrs)}",
    ))

    console.print(f"[green]vNIC added to '{lpar}' on '{system}'[/green]")
    if result.strip():
        console.print(result.strip())


@network_app.command("remove-vnic")
def network_remove_vnic(
    system: str = typer.Argument(..., help="Managed system name"),
    lpar: str = typer.Argument(..., help="LPAR name"),
    vnic_id: str = typer.Argument(..., help="vNIC ID (from list-vnics)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Remove a vNIC from an LPAR (HMC CLI via SSH)."""
    if not yes and not typer.confirm(
        f"Remove vNIC {vnic_id} from '{lpar}' on '{system}'?"
    ):
        raise typer.Abort()

    config = _ssh_config()
    payload = f"vnic_id={vnic_id}"
    result = _run(lambda: run_hmc_command(
    config,
    f"chhwres -r virtualio --rsubtype vnic -o r -m {shlex.quote(system)} "
    f"--filter lpar_names={shlex.quote(lpar)} "
    f"-a {shlex.quote(payload)}",
    ))

    console.print(f"[green]vNIC {vnic_id} removed from '{lpar}' on '{system}'[/green]")
    if result.strip():
        console.print(result.strip())


# ---------------------------------------------------------------------- #
# templates
# ---------------------------------------------------------------------- #


@templates_app.command("list")
def templates_list(as_json: bool = typer.Option(False, "--json")) -> None:
    """List partition templates in the template library."""

    async def _go():
        async with _client() as hmc:
            return await hmc.list_partition_templates()

    templates = _run(_go)

    table = None
    if not as_json:
        table = Table(title="Partition Templates")
        for col in ("Name", "UUID"):
            table.add_column(col)
        for t in templates:
            table.add_row(_g(t, "templateName", "TemplateName"), t.get("UUID") or "-")
    _output(templates, as_json, table, "No partition templates found")


@templates_app.command("show")
def templates_show(uuid: str = typer.Argument(..., help="Template UUID")) -> None:
    """Show one partition template."""

    async def _go():
        async with _client() as hmc:
            return await hmc.get_partition_template(uuid)

    t = _run(_go)

    if t is None:
        err_console.print(f"[yellow]Template {uuid} not found[/yellow]")
        raise typer.Exit(code=1)
    _print_json(t)


@templates_app.command("deploy")
def templates_deploy(
    draft_uuid: str = typer.Argument(..., help="Draft (transformed) template UUID"),
    system: str = typer.Option(..., "--system", help="Target managed system UUID"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Deploy a partition from a draft template (submits a job)."""
    if not yes and not typer.confirm(f"Deploy draft template {draft_uuid} to system {system}?"):
        raise typer.Abort()

    async def _go():
        async with _client() as hmc:
            return await hmc.deploy_partition_template(draft_uuid, system)

    job = _run(_go)

    console.print(f"[green]Submitted deploy job for template {draft_uuid}[/green]")
    _print_json(job)


# ---------------------------------------------------------------------- #
# vios
# ---------------------------------------------------------------------- #

@vios_app.command("list")
def vios_list(
    system: Optional[str] = typer.Option(None, "--system", "-s", help="Restrict to this managed system UUID"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List Virtual I/O Servers."""

    async def _go():
        async with _client() as hmc:
            return await hmc.list_vios(system)

    vios = _run(_go)

    table = None
    if not as_json:
        table = Table(title="Virtual I/O Servers")
        for col in ("Name", "ID", "UUID", "State", "Version"):
            table.add_column(col)
        for v in vios:
            table.add_row(
                _g(v, "PartitionName"),
                _g(v, "PartitionID"),
                v.get("UUID") or "-",
                _g(v, "PartitionState"),
                _g(v, "IOSLevel", "VIOSVersion", default="-"),
            )
    _output(vios, as_json, table, "No VIOS found")


@vios_app.command("power-on")
def vios_power_on(
    uuid: str = typer.Argument(..., help="VIOS UUID"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Power on a VIOS (submits a PowerOn job)."""
    if not yes and not typer.confirm(f"Really PowerOn VIOS {uuid}?"):
        raise typer.Abort()

    async def _go():
        async with _client() as hmc:
            return await hmc.power_on_vios(uuid)

    job = _run(_go)

    console.print(f"[green]Submitted PowerOn for {uuid}[/green]")
    _print_json(job)


@vios_app.command("power-off")
def vios_power_off(
    uuid: str = typer.Argument(..., help="VIOS UUID"),
    immediate: bool = typer.Option(False, "--immediate"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Power off a VIOS (submits a PowerOff job)."""
    op = "Immediate PowerOff" if immediate else "PowerOff"
    if not yes and not typer.confirm(f"Really {op} VIOS {uuid}?"):
        raise typer.Abort()

    async def _go():
        async with _client() as hmc:
            return await hmc.power_off_vios(uuid, immediate=immediate)

    job = _run(_go)

    console.print(f"[green]Submitted {op} for {uuid}[/green]")
    _print_json(job)


# ---------------------------------------------------------------------- #
# jobs
# ---------------------------------------------------------------------- #


@jobs_app.command("show")
def jobs_show(uuid: str = typer.Argument(..., help="Job UUID")) -> None:
    """Show status/result of an HMC job."""

    async def _go():
        async with _client() as hmc:
            return await hmc.get_job(uuid)

    job = _run(_go)

    if job is None:
        err_console.print(f"[yellow]Job {uuid} not found[/yellow]")
        raise typer.Exit(code=1)
    _print_json(job)


# ---------------------------------------------------------------------- #
# raw
# ---------------------------------------------------------------------- #


@raw_app.command("get")
def raw_get(path: str = typer.Argument(..., help="Path under the HMC, e.g. /rest/api/uom/VirtualSwitch")) -> None:
    """Raw GET against the HMC; prints the XML response body."""

    async def _go():
        async with _client() as hmc:
            return await hmc.raw_get(path)

    console.print(_run(_go))


@raw_app.command("post")
def raw_post(
    path: str = typer.Argument(..., help="Path to POST to"),
    body: str = typer.Argument(..., help="XML request body (string) or @file.xml"),
    content_type: str = typer.Option("application/xml", "--content-type", "-c"),
) -> None:
    """Raw POST against the HMC. Use @file.xml to read the body from a file."""

    if body.startswith("@"):
        body = open(body[1:], encoding="utf-8").read()

    async def _go():
        async with _client() as hmc:
            return await hmc.raw_post(path, body, content_type=content_type)

    console.print(_run(_go))


# ---------------------------------------------------------------------- #
# memory-pools (SSH CLI path)
# ---------------------------------------------------------------------- #

memory_pools_app = typer.Typer(help="Shared memory pools.", no_args_is_help=True)
app.add_typer(memory_pools_app, name="memory-pools")


@memory_pools_app.command("list")
def memory_pools_list(
    system_name: str = typer.Argument(..., help="Managed system name"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List shared memory pools on a managed system (HMC CLI via SSH)."""
    from .ssh import _parse_lshwres_output

    config = _ssh_config()
    output = _run(lambda: run_hmc_command(config, f"lshwres -r mempool -m {shlex.quote(system_name)}"))

    pools = _parse_lshwres_output(output)
    if as_json:
        _print_json(pools)
        return

    if not pools:
        err_console.print("[yellow]No memory pools found[/yellow]")
        return

    table = Table(title=f"Memory Pools — {system_name}")
    for key in pools[0].keys():
        table.add_column(key)
    for pool in pools:
        table.add_row(*pool.values())
    console.print(table)


@memory_pools_app.command("remove")
def memory_pools_remove(
    system_name: str = typer.Argument(..., help="Managed system name"),
    pool_name: str = typer.Argument(..., help="Memory pool name"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Remove a shared memory pool (HMC CLI via SSH).

    Performs an LPAR-assignment safety check before issuing the remove
    command.  If LPARs are still assigned to the pool the command is
    blocked and the LPAR names are reported.
    """
    if not yes and not typer.confirm(
        f"Remove memory pool '{pool_name}' on system '{system_name}'?"
    ):
        raise typer.Abort()

    from .server import hmc_remove_memory_pool

    try:
        result = hmc_remove_memory_pool(system_name, pool_name)
    except Exception as exc:
        _fail(exc)
        return

    console.print(f"[green]Memory pool '{pool_name}' removed from '{system_name}'[/green]")
    if result.strip():
        console.print(result.strip())
