"""CLI commands for HMC jobs.
"""

from __future__ import annotations


import typer

from .cli_app import (
    _client,
    _print_json,
    _run,
    err_console,
    jobs_app,
)



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


