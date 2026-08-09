"""CLI commands for clusters / Shared Storage Pools (logical units).
"""

from __future__ import annotations


import typer
from rich.table import Table

from .cli_app import (
    _first_field,
    _output,
    _print_json,
    _with_client,
    cluster_app,
    console,
)



@cluster_app.command("list")
def cluster_list(
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List Clusters (VIOS node sets sharing a storage pool)."""

    clusters = _with_client(lambda hmc: hmc.list_clusters())

    table = None
    if not as_json:
        table = Table(title="Clusters")
        for col in ("Name", "UUID"):
            table.add_column(col)
        for c in clusters:
            table.add_row(_first_field(c, "ClusterName"), c.get("UUID") or "-")
    _output(clusters, as_json, table, "No clusters found")


@cluster_app.command("list-ssps")
def cluster_list_ssps(
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List Shared Storage Pools (capacity, free space, logical units)."""

    ssps = _with_client(lambda hmc: hmc.list_shared_storage_pools())

    table = None
    if not as_json:
        table = Table(title="Shared Storage Pools")
        for col in ("Name", "UUID", "Capacity (GB)", "Free (GB)"):
            table.add_column(col)
        for s in ssps:
            table.add_row(
                _first_field(s, "StoragePoolName"),
                s.get("UUID") or "-",
                _first_field(s, "Capacity"),
                _first_field(s, "FreeSpace"),
            )
    _output(ssps, as_json, table, "No shared storage pools found")


@cluster_app.command("create-lu")
def cluster_create_lu(
    cluster: str = typer.Argument(..., help="Cluster UUID"),
    name: str = typer.Option(..., "--name", "-n", help="Logical unit name"),
    size: int = typer.Option(..., "--size", help="Size in GB"),
    lu_type: str = typer.Option("THIN", "--type", help="THIN or THICK"),
    device_type: str = typer.Option("VirtualIO_Disk", "--device-type", help="VirtualIO_Disk or VirtualIO_Image"),
    cloned_from: str | None = typer.Option(None, "--cloned-from", help="Source LU UDID to clone"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Create a Logical Unit in a Cluster/SSP (submits a job)."""
    if not yes and not typer.confirm(f"Create {size} GB {lu_type} LU '{name}' in cluster {cluster}?"):
        raise typer.Abort()

    job = _with_client(
        lambda hmc: hmc.create_logical_unit(cluster, name, size, lu_type, device_type, cloned_from)
    )

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

    job = _with_client(lambda hmc: hmc.delete_logical_unit(cluster, udid))

    console.print(f"[green]Submitted DeleteLogicalUnit job for {udid}[/green]")
    _print_json(job)


