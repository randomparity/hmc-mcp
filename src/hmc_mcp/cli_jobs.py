"""CLI commands for HMC jobs.
"""

from __future__ import annotations


import typer

from .cli_app import (
    _print_json,
    _with_client,
    err_console,
    jobs_app,
)



@jobs_app.command("show")
def jobs_show(uuid: str = typer.Argument(..., help="Job UUID")) -> None:
    """Show status/result of an HMC job."""

    job = _with_client(lambda hmc: hmc.get_job(uuid))

    if job is None:
        err_console.print(f"[yellow]Job {uuid} not found[/yellow]")
        raise typer.Exit(code=1)
    _print_json(job)


