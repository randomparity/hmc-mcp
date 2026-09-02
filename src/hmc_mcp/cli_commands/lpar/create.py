"""CLI command for creating an LPAR."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import typer

from ...documents import PARTITION_TYPES, LparResources
from ...operations.lpar.core import LparCreation
from ...operations.lpar.workflows import create_lpar
from ...ssh.lpar import validate_caller_token
from ..output import console, err_console, print_json, usage_error
from ..runtime import with_client
from .config import _load_pcie_assignments


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
        usage_error(
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

    result = with_client(
        lambda hmc: create_lpar(
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
    )

    console.print(f"[green]Created LPAR '{name}'[/green]")
    print_json(result.lpar)
    for warning in result.warnings:
        err_console.print(f"[yellow]Warning: {warning}[/yellow]")
    if result.steps:
        print_json(asdict(result))
    if not result.workflow_completed:
        raise typer.Exit(1)


def register_commands(group: typer.Typer) -> None:
    """Register this module’s commands on *group*."""
    group.command("create")(lpars_create)
