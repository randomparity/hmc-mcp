"""CLI commands for LPAR power and deletion lifecycle operations."""

from __future__ import annotations

import typer

from ...jobs import validate_wait_timing
from ...operations.lpar.core import delete_lpar, power_lpar
from ..output import console, err_console, print_json
from ..runtime import client, run


def lpars_power_on(
    name_or_uuid: str = typer.Argument(..., help="Partition name or UUID"),
    wait: bool = typer.Option(
        False, "--wait/--no-wait", help="Wait for job completion"
    ),
    timeout: int = typer.Option(300, "--timeout", help="Seconds to wait (with --wait)"),
    interval: int = typer.Option(
        5, "--interval", help="Poll interval seconds (with --wait)"
    ),
    force: bool = typer.Option(False, "--force", help="Submit even if already running"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    system: str | None = typer.Option(
        None,
        "--system",
        "-s",
        help="Managed system name or UUID; with HMC_AUTHORIZE_POWER_OPERATIONS it also spares the ownership guard a fleet-wide search",
    ),
    ownership_override: bool = typer.Option(
        False,
        "--ownership-override",
        help="Bypass ownership protection after operator approval; no effect unless HMC_AUTHORIZE_POWER_OPERATIONS is set",
    ),
) -> None:
    """Power on an LPAR (submits a PowerOn job)."""
    _power_lpar(
        name_or_uuid,
        on=True,
        force=force,
        yes=yes,
        wait=wait,
        timeout=timeout,
        interval=interval,
        system=system,
        ownership_override=ownership_override,
    )


def lpars_power_off(
    name_or_uuid: str = typer.Argument(..., help="Partition name or UUID"),
    immediate: bool = typer.Option(
        False, "--immediate", help="Immediate power off (no graceful shutdown)"
    ),
    wait: bool = typer.Option(
        False, "--wait/--no-wait", help="Wait for job completion"
    ),
    timeout: int = typer.Option(300, "--timeout", help="Seconds to wait (with --wait)"),
    interval: int = typer.Option(
        5, "--interval", help="Poll interval seconds (with --wait)"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    system: str | None = typer.Option(
        None,
        "--system",
        "-s",
        help="Managed system name or UUID; with HMC_AUTHORIZE_POWER_OPERATIONS it also spares the ownership guard a fleet-wide search",
    ),
    ownership_override: bool = typer.Option(
        False,
        "--ownership-override",
        help="Bypass ownership protection after operator approval; no effect unless HMC_AUTHORIZE_POWER_OPERATIONS is set",
    ),
) -> None:
    """Power off an LPAR (submits a PowerOff job)."""
    _power_lpar(
        name_or_uuid,
        on=False,
        immediate=immediate,
        yes=yes,
        wait=wait,
        timeout=timeout,
        interval=interval,
        system=system,
        ownership_override=ownership_override,
    )


def _power_lpar(
    name_or_uuid: str,
    on: bool,
    immediate: bool = False,
    force: bool = False,
    yes: bool = False,
    wait: bool = False,
    timeout: int = 300,
    interval: int = 5,
    system: str | None = None,
    ownership_override: bool = False,
) -> None:
    validate_wait_timing(wait, timeout, interval)
    if not yes:
        op = "PowerOn" if on else ("Immediate PowerOff" if immediate else "PowerOff")
        if not typer.confirm(f"Really submit {op} for partition '{name_or_uuid}'?"):
            err_console.print("Aborted.")
            raise typer.Abort()

    async def _go():
        async with client() as hmc:
            return await power_lpar(
                hmc,
                system,
                name_or_uuid,
                power_on=on,
                immediate=immediate,
                force=force,
                wait=wait,
                timeout_seconds=timeout,
                poll_interval=interval,
                ownership_override=ownership_override,
            )

    result = run(_go)
    uuid, job = result.lpar_uuid, result.job
    if job and job.get("already_running"):
        console.print(f"[yellow]{job['message']}[/yellow]")
        print_json(job)
        return
    console.print(f"[green]Job submitted[/green] for {uuid}")
    print_json(job)


def lpars_delete(
    name_or_uuid: str = typer.Argument(..., help="Partition name or UUID"),
    system: str = typer.Option(
        ..., "--system", "-s", help="Managed system name or UUID"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    ownership_override: bool = typer.Option(
        False,
        "--ownership-override",
        help="Bypass ownership protection after operator approval",
    ),
) -> None:
    """Delete (destroy) an LPAR. It must be powered off first."""

    async def _go():
        async with client() as hmc:
            if not yes:
                if not typer.confirm(
                    f"Permanently DELETE partition '{name_or_uuid}'? This cannot be undone."
                ):
                    raise typer.Abort()
            return await delete_lpar(
                hmc,
                system,
                name_or_uuid,
                ownership_override=ownership_override,
            )

    uuid = run(_go)
    console.print(f"[green]Deleted LPAR {uuid}[/green]")


def register_commands(group: typer.Typer) -> None:
    """Register this module’s commands on *group*."""
    group.command("power-on")(lpars_power_on)
    group.command("power-off")(lpars_power_off)
    group.command("delete")(lpars_delete)
