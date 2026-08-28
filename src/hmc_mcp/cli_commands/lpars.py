"""CLI commands for core LPAR lifecycle operations."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import typer
from pydantic import TypeAdapter, ValidationError
from rich.table import Table

from ..documents import (
    PARTITION_TYPES,
    LparResources,
)
from ..jobs import validate_wait_timing
from ..operations.lpar.assignments import (
    LparPcieAssignments,
)
from ..operations.lpar.decommission import decommission_lpar
from ..operations.lpar.core import (
    LparCreation,
    delete_lpar,
    power_lpar,
)
from ..operations.lpar.dlpar import modify_lpar
from ..operations.lpar.workflows import create_lpar
from ..ssh.lpar import validate_caller_token
from .app import (
    _client,
    _partition_not_found,
    _print_json,
    _run,
    _usage_error,
    console,
    err_console,
)


def _load_pcie_assignments(path: Path | None) -> LparPcieAssignments:
    """Load the shared assignment schema from a JSON document."""
    if path is None:
        return LparPcieAssignments()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return TypeAdapter(LparPcieAssignments).validate_python(payload)
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        _usage_error(f"Cannot load --pcie-assignments {path}: {error}")
        raise AssertionError("_usage_error must raise") from error


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
        async with _client() as hmc:
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

    result = _run(_go)
    uuid, job = result.lpar_uuid, result.job
    if job and job.get("already_running"):
        console.print(f"[yellow]{job['message']}[/yellow]")
        _print_json(job)
        return
    console.print(f"[green]Job submitted[/green] for {uuid}")
    _print_json(job)


def lpars_create(
    name: str = typer.Argument(..., help="Name for the new partition"),
    system: str = typer.Option(
        ..., "--system", "-s", help="Target managed system UUID"
    ),
    partition_type: str = typer.Option(
        "AIX/Linux", "--type", help=f"One of: {', '.join(PARTITION_TYPES)}"
    ),
    partition_id: int | None = typer.Option(
        None, "--id", help="Partition ID (auto-assigned if omitted)"
    ),
    min_memory: int = typer.Option(256, "--min-mem", help="Minimum memory (MiB)"),
    memory: int = typer.Option(4096, "--mem", help="Desired memory (MiB)"),
    max_memory: int = typer.Option(8192, "--max-mem", help="Maximum memory (MiB)"),
    dedicated: bool = typer.Option(
        False, "--dedicated", help="Dedicated CPUs instead of shared"
    ),
    min_procs: float | None = typer.Option(
        None, "--min-procs", help="Min processing units / dedicated CPUs"
    ),
    procs: float | None = typer.Option(
        None, "--procs", help="Desired processing units / dedicated CPUs"
    ),
    max_procs: float | None = typer.Option(
        None, "--max-procs", help="Max processing units / dedicated CPUs"
    ),
    min_vcpus: int | None = typer.Option(
        None, "--min-vcpus", help="Min virtual processors (shared)"
    ),
    vcpus: int | None = typer.Option(
        1, "--vcpus", help="Desired virtual processors (shared)"
    ),
    max_vcpus: int | None = typer.Option(
        2, "--max-vcpus", help="Max virtual processors (shared)"
    ),
    capped: bool = typer.Option(
        False, "--capped", help="Cap shared CPU (default uncapped)"
    ),
    pcie_assignments: Path | None = typer.Option(
        None,
        "--pcie-assignments",
        help="JSON file using the declarative LparPcieAssignments schema",
    ),
    caller_token: str | None = typer.Option(
        None,
        "--caller-token",
        help="Optional tracking reference embedded in the partition description "
        "as '\\[caller <token>]' (ADR 0064); 1–64 printable ASCII characters, "
        'no whitespace or , = " [ ] \\',
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Create a new LPAR on a managed system.

    Creates the partition powered off with a default profile; storage/network
    and boot settings are configured afterwards via the HMC.
    """
    if caller_token is not None:
        validate_caller_token(caller_token)
    if partition_type not in PARTITION_TYPES:
        _usage_error(
            f"--type must be one of {', '.join(PARTITION_TYPES)}, got {partition_type!r}"
        )
    if not yes:
        typer.confirm(
            f"Create LPAR '{name}' ({partition_type}, {memory} MiB) on system {system}?",
            abort=True,
        )
    resources = LparResources(
        min_memory=min_memory,
        desired_memory=memory,
        max_memory=max_memory,
        dedicated=dedicated,
        min_procs=min_procs,
        desired_procs=procs,
        max_procs=max_procs,
        min_vcpus=min_vcpus,
        desired_vcpus=vcpus,
        max_vcpus=max_vcpus,
        uncapped=not capped,
    )
    assignments = _load_pcie_assignments(pcie_assignments)

    async def _go():
        async with _client() as hmc:
            return await create_lpar(
                hmc,
                system,
                LparCreation(
                    name,
                    partition_type,
                    resources,
                    partition_id=partition_id,
                    caller_token=caller_token,
                ),
                assignments,
            )

    result = _run(_go)

    console.print(f"[green]Created LPAR '{name}'[/green]")
    _print_json(result.lpar)
    for warning in result.warnings:
        err_console.print(f"[yellow]Warning: {warning}[/yellow]")
    if result.steps:
        _print_json(asdict(result))
    if not result.workflow_completed:
        raise typer.Exit(1)


def lpars_modify(
    name_or_uuid: str = typer.Argument(..., help="Partition name or UUID"),
    system: str | None = typer.Option(
        None, "--system", "-s", help="Managed system name or UUID (required for rename)"
    ),
    new_name: str | None = typer.Option(None, "--name", help="Rename the partition"),
    min_memory: int | None = typer.Option(
        None, "--min-mem", help="Minimum memory (MiB)"
    ),
    memory: int | None = typer.Option(None, "--mem", help="Desired memory (MiB)"),
    max_memory: int | None = typer.Option(
        None, "--max-mem", help="Maximum memory (MiB)"
    ),
    dedicated: bool | None = typer.Option(
        None,
        "--dedicated/--no-dedicated",
        help="Assign dedicated CPUs (default: leave unchanged)",
    ),
    min_procs: float | None = typer.Option(None, "--min-procs"),
    procs: float | None = typer.Option(None, "--procs"),
    max_procs: float | None = typer.Option(None, "--max-procs"),
    min_vcpus: int | None = typer.Option(None, "--min-vcpus"),
    vcpus: int | None = typer.Option(None, "--vcpus"),
    max_vcpus: int | None = typer.Option(None, "--max-vcpus"),
    capped: bool | None = typer.Option(
        None, "--capped/--uncapped", help="Cap shared CPU (default: leave unchanged)"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    ownership_override: bool = typer.Option(
        False,
        "--ownership-override",
        help="Bypass ownership protection after operator approval",
    ),
    pcie_assignments: Path | None = typer.Option(
        None,
        "--pcie-assignments",
        help="JSON file using the declarative LparPcieAssignments schema",
    ),
) -> None:
    """Change an LPAR's name and/or resource assignment (memory / CPU).

    Only options you pass are changed. On a running partition these are
    dynamic (DLPAR) operations and need RMC up; otherwise they apply on next
    activation.
    """
    assignments = _load_pcie_assignments(pcie_assignments)
    if (
        all(
            v is None
            for v in (
                new_name,
                min_memory,
                memory,
                max_memory,
                min_procs,
                procs,
                max_procs,
                min_vcpus,
                vcpus,
                max_vcpus,
                dedicated,
                capped,
            )
        )
        and assignments == LparPcieAssignments()
    ):
        _usage_error("Nothing to change — pass at least one option")
    if new_name is not None and system is None:
        _usage_error("--system is required when renaming an LPAR")
    if assignments != LparPcieAssignments() and system is None:
        _usage_error("--system is required when assigning PCIe resources")
    resources = LparResources(
        min_memory=min_memory,
        desired_memory=memory,
        max_memory=max_memory,
        dedicated=dedicated,
        min_procs=min_procs,
        desired_procs=procs,
        max_procs=max_procs,
        min_vcpus=min_vcpus,
        desired_vcpus=vcpus,
        max_vcpus=max_vcpus,
        uncapped=None if capped is None else not capped,
    )
    if not yes and not typer.confirm(f"Apply changes to '{name_or_uuid}'?"):
        raise typer.Abort()

    async def _go():
        async with _client() as hmc:
            return await modify_lpar(
                hmc,
                system,
                name_or_uuid,
                resources,
                assignments,
                new_name=new_name,
                ownership_override=ownership_override,
            )

    result = _run(_go)

    if result.lpar is None:
        _partition_not_found(name_or_uuid)
    uuid = result.lpar.get("UUID", name_or_uuid)
    console.print(f"[green]Modified LPAR {uuid}[/green]")
    _print_json(asdict(result))


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
        async with _client() as hmc:
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

    uuid = _run(_go)
    console.print(f"[green]Deleted LPAR {uuid}[/green]")


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
    group.command("power-on")(lpars_power_on)
    group.command("power-off")(lpars_power_off)
    group.command("create")(lpars_create)
    group.command("modify")(lpars_modify)
    group.command("delete")(lpars_delete)
    group.command("decommission")(lpars_decommission)
