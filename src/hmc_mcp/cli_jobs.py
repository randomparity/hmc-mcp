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


@jobs_app.command("list")
def jobs_list(
    limit: int = typer.Option(20, "--limit", "-n", help="Maximum number of jobs to return"),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """List recent HMC jobs (audit view of recent activity)."""

    jobs = _with_client(lambda hmc: hmc.list_uom("Job"))
    if limit > 0:
        jobs = jobs[:limit]
    _print_json(jobs)


@jobs_app.command("wait")
def jobs_wait(
    uuid: str = typer.Argument(..., help="Job UUID"),
    timeout: int = typer.Option(300, "--timeout", "-t", help="Maximum seconds to wait"),
    interval: int = typer.Option(5, "--interval", "-i", help="Seconds between polls"),
) -> None:
    """Poll a job until it reaches a terminal status (COMPLETED/FAILED/EXCEPTION)."""

    job = _with_client(lambda hmc: hmc.wait_for_job(uuid, timeout, interval))

    if job is None:
        err_console.print(f"[yellow]Job {uuid} not found[/yellow]")
        raise typer.Exit(code=1)
    _print_json(job)


