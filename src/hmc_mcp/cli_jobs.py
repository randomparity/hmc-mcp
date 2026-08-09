"""CLI commands for HMC jobs.
"""

from __future__ import annotations


import typer

from .cli_app import (
    _output,
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


@jobs_app.command("list")
def jobs_list(
    limit: int = typer.Option(20, "--limit", "-n", help="Maximum number of jobs to return"),
    as_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """List recent HMC jobs."""

    jobs = _with_client(lambda hmc: hmc.list_uom("Job"))
    jobs = jobs[:limit]
    _output(jobs, as_json, empty_msg="No jobs found")


