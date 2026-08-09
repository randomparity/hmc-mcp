"""CLI commands for PCM performance/capacity metrics.
"""

from __future__ import annotations


import typer

from .client import HMCError
from .cli_app import (
    _client,
    _print_json,
    _run,
    _with_client,
    console,
    err_console,
    metrics_app,
)



@metrics_app.command("prefs")
def metrics_prefs(
    category: str = typer.Argument(..., help="e.g. ManagedSystem, LogicalPartition"),
    uuid: str = typer.Argument(..., help="Resource UUID"),
) -> None:
    """Show PCM monitoring preferences for a resource."""

    prefs = _with_client(lambda hmc: hmc.get_pcm_preferences(category, uuid))

    _print_json(prefs)


@metrics_app.command("set-prefs")
def metrics_set_prefs(
    category: str = typer.Argument(..., help="e.g. ManagedSystem"),
    uuid: str = typer.Argument(..., help="Resource UUID"),
    ltm: bool | None = typer.Option(None, "--ltm/--no-ltm", help="Long-term monitoring"),
    aggregation: bool | None = typer.Option(None, "--aggregation/--no-aggregation"),
    stm: bool | None = typer.Option(None, "--stm/--no-stm", help="Short-term monitoring"),
    energy: bool | None = typer.Option(None, "--energy/--no-energy", help="Energy monitoring"),
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
    end: str | None = typer.Option(None, "--end", help="End TS (optional)"),
    samples: int | None = typer.Option(None, "--samples", help="Number of samples"),
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
            # A 404 means the newest document aged out of PCM retention;
            # report that as no data rather than an error.
            try:
                return await hmc.fetch_json(links[-1]["link"])
            except HMCError as exc:
                if exc.status_code == 404:
                    return {}
                raise

    result = _run(_go)

    _print_json(result)


