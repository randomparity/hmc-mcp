"""CLI command for modifying LPAR resources and identity."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import typer

from ...documents import LparResources
from ...operations.lpar.assignments import LparPcieAssignments
from ...operations.lpar.dlpar import modify_lpar
from ..runtime import client, run
from ..output import partition_not_found, print_json, usage_error, console
from .config import _load_pcie_assignments


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
        usage_error("Nothing to change — pass at least one option")
    if new_name is not None and system is None:
        usage_error("--system is required when renaming an LPAR")
    if assignments != LparPcieAssignments() and system is None:
        usage_error("--system is required when assigning PCIe resources")
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
        async with client() as hmc:
            return await modify_lpar(
                hmc,
                system,
                name_or_uuid,
                resources,
                assignments,
                new_name=new_name,
                ownership_override=ownership_override,
            )

    result = run(_go)

    if result.lpar is None:
        partition_not_found(name_or_uuid)
    uuid = result.lpar.get("UUID", name_or_uuid)
    console.print(f"[green]Modified LPAR {uuid}[/green]")
    print_json(asdict(result))


def register_commands(group: typer.Typer) -> None:
    """Register this module’s commands on *group*."""
    group.command("modify")(lpars_modify)
