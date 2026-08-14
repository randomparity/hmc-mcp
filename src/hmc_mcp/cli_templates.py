"""CLI commands for the partition template library."""

from __future__ import annotations


import typer
from rich.table import Table

from .cli_app import (
    _first_field,
    _output,
    _print_json,
    _usage_error,
    _with_client,
    console,
    templates_app,
)
from .error_translation import run_with_error_translation, translate_template_error
from .jobs import validate_wait_timing
from .operations_templates import deploy_partition_template


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
            table.add_row(
                _first_field(t, "templateName", "TemplateName"), t.get("UUID") or "-"
            )
    _output(templates, as_json, table, "No partition templates found")


@templates_app.command("show")
def templates_show(uuid: str = typer.Argument(..., help="Template UUID")) -> None:
    """Show one partition template."""

    t = _with_client(
        lambda hmc: run_with_error_translation(
            lambda: hmc.get_partition_template(uuid), translate_template_error
        )
    )

    _print_json(t)


@templates_app.command("deploy")
def templates_deploy(
    draft_uuid: str = typer.Argument(..., help="Draft (transformed) template UUID"),
    system: str = typer.Option(
        ..., "--system", help="Target managed system name or UUID"
    ),
    wait: bool = typer.Option(False, "--wait", help="Wait for the deploy job"),
    timeout_seconds: int = typer.Option(300, "--timeout"),
    poll_interval: int = typer.Option(5, "--poll-interval"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Deploy a partition from a draft template (submits a job)."""
    try:
        validate_wait_timing(wait, timeout_seconds, poll_interval)
    except ValueError as exc:
        _usage_error(str(exc))
    if not yes and not typer.confirm(
        f"Deploy draft template {draft_uuid} to system {system}?"
    ):
        raise typer.Abort()

    result = _with_client(
        lambda hmc: deploy_partition_template(
            hmc,
            draft_uuid,
            system,
            wait=wait,
            timeout_seconds=timeout_seconds,
            poll_interval=poll_interval,
        )
    )

    console.print(f"[green]Deploy job for template {draft_uuid}[/green]")
    _print_json(result)
