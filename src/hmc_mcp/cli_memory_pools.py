"""CLI commands for shared memory pools (HMC CLI via SSH)."""

from __future__ import annotations


import typer
from rich.table import Table

from .cli_app import (
    _print_json,
    _run,
    _ssh_config,
    console,
    err_console,
    memory_pools_app,
)

from .ssh_commands import list_memory_pools, remove_memory_pool


@memory_pools_app.command("list")
def memory_pools_list(
    system_name: str = typer.Argument(..., help="Managed system name"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List shared memory pools on a managed system (HMC CLI via SSH)."""
    config = _ssh_config()
    pools = _run(lambda: list_memory_pools(config, system_name))
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

    config = _ssh_config()
    result = _run(lambda: remove_memory_pool(config, system_name, pool_name))

    console.print(
        f"[green]Memory pool '{pool_name}' removed from '{system_name}'[/green]"
    )
    if result.strip():
        console.print(result.strip())
