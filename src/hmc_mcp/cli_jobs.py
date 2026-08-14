"""CLI commands for HMC jobs."""

from __future__ import annotations


import typer

from .cli_app import (
    _output,
    _print_json,
    _run,
    _usage_error,
    _client,
    _with_client,
    console,
    err_console,
    jobs_app,
)


@jobs_app.command("show")
def jobs_show(
    uuid: str = typer.Argument(..., help="Job UUID"),
    job_href: str | None = typer.Option(
        None, "--job-href", help="SELF link returned by job submission"
    ),
) -> None:
    """Show status/result of an HMC job."""

    job = _with_client(lambda hmc: hmc.get_job(uuid, job_href=job_href))

    if job is None:
        err_console.print(f"[yellow]Job {uuid} not found[/yellow]")
        raise typer.Exit(code=1)
    _print_json(job)


@jobs_app.command("list")
def jobs_list(
    limit: int = typer.Option(
        20, "--limit", "-n", help="Maximum number of jobs to return"
    ),
    as_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """List recent HMC jobs."""
    if limit < 0:
        _usage_error("--limit must be greater than or equal to 0")

    jobs = _with_client(lambda hmc: hmc.list_uom("Job"))
    jobs = jobs[:limit]
    _output(jobs, as_json, empty_msg="No jobs found")


@jobs_app.command("wait")
def jobs_wait(
    uuid: str = typer.Argument(..., help="Job UUID to wait on"),
    timeout: int = typer.Option(300, "--timeout", "-t", help="Maximum seconds to wait"),
    interval: int = typer.Option(
        5, "--interval", "-i", help="Poll interval in seconds"
    ),
    job_href: str | None = typer.Option(
        None, "--job-href", help="SELF link returned by job submission"
    ),
) -> None:
    """Wait for an HMC job to reach a terminal state (COMPLETED / FAILED / EXCEPTION).

    Prints the final job entry once a terminal state is reached or the
    timeout elapses.
    """

    async def _go():
        async with _client() as hmc:
            return await hmc.wait_for_job(uuid, timeout, interval, job_href=job_href)

    job = _run(_go)

    if job is None:
        err_console.print(f"[yellow]Job {uuid} not found[/yellow]")
        raise typer.Exit(code=1)
    status = (job.get("Resource") or {}).get("Status", "unknown")
    console.print(f"[green]Job {uuid} status: {status}[/green]")
    _print_json(job)
