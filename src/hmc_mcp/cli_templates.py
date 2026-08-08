"""CLI commands for the partition template library.
"""

from __future__ import annotations


import typer
from rich.table import Table

from .cli_app import (
    _client,
    _g,
    _output,
    _print_json,
    _run,
    console,
    err_console,
    templates_app,
)



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


