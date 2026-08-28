"""CLI commands for HMC jobs."""

from __future__ import annotations

from dataclasses import asdict

import typer

from ..operations import jobs as operations_jobs

from .app import (
    _output,
    _print_json,
    _usage_error,
    _with_client,
    console,
    err_console,
)


def jobs_show(
    job_id: str = typer.Argument(..., help="Job UUID or JobID"),
    job_href: str | None = typer.Option(
        None, "--job-href", help="SELF link returned by job submission"
    ),
) -> None:
    """Show status/result of an HMC job."""

    outcome = _with_client(
        lambda hmc: operations_jobs.get_job(hmc, job_id, job_href=job_href)
    )

    if not outcome.found:
        err_console.print(f"[yellow]Job {job_id} not found[/yellow]")
        raise typer.Exit(code=1)
    _print_json(asdict(outcome))


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


def jobs_wait(
    job_id: str = typer.Argument(..., help="Job UUID or JobID to wait on"),
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

    outcome = _with_client(
        lambda hmc: operations_jobs.wait_for_job(
            hmc,
            job_id,
            job_href=job_href,
            timeout_seconds=timeout,
            poll_interval=interval,
        )
    )

    if not outcome.found:
        err_console.print(f"[yellow]Job {job_id} not found[/yellow]")
        raise typer.Exit(code=1)
    status = outcome.status or "unknown"
    console.print(f"[green]Job {job_id} status: {status}[/green]")
    _print_json(asdict(outcome))


def register_commands(group: typer.Typer) -> None:
    """Register this module’s commands on *group*."""
    group.command("show")(jobs_show)
    group.command("list")(jobs_list)
    group.command("wait")(jobs_wait)
