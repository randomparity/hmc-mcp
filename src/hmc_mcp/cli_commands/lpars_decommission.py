"""CLI command for the LPAR decommission workflow."""

from __future__ import annotations

from dataclasses import asdict

import typer
from rich.table import Table

from ..operations.lpar.decommission import decommission_lpar
from .runtime import _client, _run
from .output import _print_json, console


def lpars_decommission(
    name_or_uuid: str = typer.Argument(..., help="Partition name or UUID"),
    system: str = typer.Option(
        ..., "--system", "-s", help="Managed system name or UUID"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Inventory the blast radius without mutating"
    ),
    ownership_override: bool = typer.Option(
        False,
        "--ownership-override",
        help="Bypass ownership protection after operator approval",
    ),
    immediate: bool = typer.Option(
        False, "--immediate", help="Request immediate shutdown before deletion"
    ),
    timeout_seconds: int = typer.Option(
        300, "--timeout-seconds", help="Seconds to wait for the power-off job"
    ),
    poll_interval: int = typer.Option(
        5, "--poll-interval", help="Poll interval seconds for the power-off job"
    ),
    as_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Inventory and optionally decommission one LPAR."""
    if not dry_run and not yes:
        typer.confirm(
            f"Decommission LPAR '{name_or_uuid}' on system '{system}'? "
            "This powers it off, detaches adapters, and deletes it.",
            abort=True,
        )

    async def _go():
        async with _client() as hmc:
            return await decommission_lpar(
                hmc,
                system,
                name_or_uuid,
                dry_run=dry_run,
                ownership_override=ownership_override,
                immediate=immediate,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
            )

    result = _run(_go)

    if as_json:
        _print_json(asdict(result))
    else:
        if result.dry_run:
            console.print(
                "[yellow]DRY RUN — decommission plan generated; no adapters or LPARs "
                "were deleted[/yellow]"
            )
        elif result.workflow_completed:
            console.print(
                f"[green]LPAR '{name_or_uuid}' decommissioned successfully[/green]"
            )
        else:
            console.print(
                f"[yellow]LPAR '{name_or_uuid}' was not fully decommissioned — "
                "check step results[/yellow]"
            )

        table = Table(title=f"Decommission steps: {name_or_uuid}")
        table.add_column("Step", style="cyan")
        table.add_column("Status", style="green")
        table.add_column("Result")
        for step in result.steps:
            status = step.status
            style = (
                "green"
                if status == "ok"
                else ("yellow" if status in ("dry_run", "skipped") else "red")
            )
            table.add_row(
                step.step,
                f"[{style}]{status}[/{style}]",
                "-" if step.result is None else str(step.result),
            )
        console.print(table)

        if result.warnings:
            for warning in result.warnings:
                console.print(f"[yellow]Warning: {warning}[/yellow]")

    if not result.dry_run and not result.workflow_completed:
        raise typer.Exit(1)


def register_commands(group: typer.Typer) -> None:
    """Register this module’s commands on *group*."""
    group.command("decommission")(lpars_decommission)
