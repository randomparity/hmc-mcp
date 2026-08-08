"""CLI commands for clusters / Shared Storage Pools (logical units).
"""

from __future__ import annotations

from typing import Optional

import typer
from rich.table import Table

from .cli_app import (
    _client,
    _g,
    _output,
    _print_json,
    _run,
    cluster_app,
    console,
)



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


