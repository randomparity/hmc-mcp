"""CLI commands for the partition template library.
"""

from __future__ import annotations


import typer
from rich.table import Table

from .cli_app import (
    _first_field,
    _output,
    _print_json,
    _with_client,
    console,
    err_console,
    templates_app,
)
from .error_translation import run_with_error_translation, translate_template_error



@templates_app.command("list")
def templates_list(as_json: bool = typer.Option(False, "--json")) -> None:
    """List partition templates in the template library."""

    templates = _with_client(
        lambda hmc: run_with_error_translation(
            hmc.list_partition_templates, translate_template_error
        )
    )

    table = None
    if not as_json:
        table = Table(title="Partition Templates")
        for col in ("Name", "UUID"):
            table.add_column(col)
        for t in templates:
            table.add_row(_first_field(t, "templateName", "TemplateName"), t.get("UUID") or "-")
    _output(templates, as_json, table, "No partition templates found")


@templates_app.command("show")
def templates_show(uuid: str = typer.Argument(..., help="Template UUID")) -> None:
    """Show one partition template."""

    t = _with_client(
        lambda hmc: run_with_error_translation(
            lambda: hmc.get_partition_template(uuid), translate_template_error
        )
    )

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

    job = _with_client(
        lambda hmc: run_with_error_translation(
            lambda: hmc.deploy_partition_template(draft_uuid, system),
            translate_template_error,
        )
    )

    console.print(f"[green]Submitted deploy job for template {draft_uuid}[/green]")
    _print_json(job)

