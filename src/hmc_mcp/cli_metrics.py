"""CLI commands for PCM performance/capacity metrics."""

from __future__ import annotations


import typer

from .cli_app import (
    _client,
    _print_json,
    _run,
    _usage_error,
    _with_client,
    console,
    metrics_app,
)
from .operations_pcm import (
    get_pcm_preferences,
    metric_data,
    metric_links,
    preference_flags,
    set_pcm_preferences,
)


@metrics_app.command("prefs")
def metrics_prefs(
    category: str = typer.Argument(..., help="e.g. ManagedSystem, LogicalPartition"),
    resource_uuid: str = typer.Argument(..., help="Resource name or UUID"),
) -> None:
    """Show PCM monitoring preferences for a resource."""

    prefs = _with_client(lambda hmc: get_pcm_preferences(hmc, category, resource_uuid))

    _print_json(prefs)


@metrics_app.command("set-prefs")
def metrics_set_prefs(
    category: str = typer.Argument(..., help="e.g. ManagedSystem"),
    resource_uuid: str = typer.Argument(..., help="Resource name or UUID"),
    ltm: bool | None = typer.Option(
        None, "--ltm/--no-ltm", help="Long-term monitoring"
    ),
    aggregation: bool | None = typer.Option(None, "--aggregation/--no-aggregation"),
    stm: bool | None = typer.Option(
        None, "--stm/--no-stm", help="Short-term monitoring"
    ),
    compute_ltm: bool | None = typer.Option(
        None, "--compute-ltm/--no-compute-ltm", help="Compute long-term monitoring"
    ),
    energy: bool | None = typer.Option(
        None, "--energy/--no-energy", help="Energy monitoring"
    ),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Enable/disable PCM data collection for a resource.

    Mutates HMC monitoring configuration, so it is gated by the same
    --yes/confirm convention as every other mutating CLI command.
    """
    flags = preference_flags(ltm, aggregation, stm, compute_ltm, energy)
    if not flags:
        _usage_error("No flags supplied; nothing to change.")

    if not yes and not typer.confirm(
        f"Enable/disable PCM monitoring on {category} {resource_uuid}?"
    ):
        raise typer.Abort()

    _with_client(lambda hmc: set_pcm_preferences(hmc, category, resource_uuid, flags))

    console.print(f"[green]Updated {category} {resource_uuid}: {flags}[/green]")


@metrics_app.command("show")
def metrics_show(
    category: str = typer.Argument(..., help="e.g. ManagedSystem, LogicalPartition"),
    resource_uuid: str = typer.Argument(..., help="Resource name or UUID"),
    start: str = typer.Option(..., "--start", help="Start TS yyyy-MM-ddTHH:mm:ssZ"),
    end: str | None = typer.Option(None, "--end", help="End TS (optional)"),
    samples: int | None = typer.Option(None, "--samples", help="Number of samples"),
    aggregated: bool = typer.Option(
        False, "--aggregated", help="Use aggregated (long-term) metrics"
    ),
    fetch: bool = typer.Option(
        False, "--fetch", help="Also download the latest JSON doc"
    ),
) -> None:
    """Get PCM metrics (processed by default; --aggregated for rollups)."""

    async def _go():
        async with _client() as hmc:
            kind = "aggregated" if aggregated else "processed"
            operation = metric_data if fetch else metric_links
            return await operation(
                hmc, category, resource_uuid, kind, start, end, samples
            )

    result = _run(_go)

    _print_json(result)
