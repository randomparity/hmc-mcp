"""CLI commands for LPARs: list/show/state, power, LPM, lifecycle, description, MSP, and processor compatibility."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import json

import typer
from pydantic import TypeAdapter, ValidationError
from rich.table import Table
from typing import cast

from .app import (
    _client,
    _partition_not_found,
    _print_json,
    _resolve_partition_uuid,
    _run,
    _ssh_client,
    _ssh_config,
    _with_client,
    _usage_error,
    console,
    err_console,
    lpars_app,
)

from ..jobs import validate_wait_timing
from ..lpar_ownership import set_lpar_ownership_description
from ..operations.lpar import ProcessorCompatibilityMode
from ..operations.lpar import (
    LparCreation,
    create_and_stamp_lpar,
    delete_lpar,
    power_lpar,
    rename_lpar,
)
from ..operations.assignments import (
    LparPcieAssignments,
    _apply_validated_lpar_pcie_assignments,
    prevalidate_lpar_pcie_assignments,
)
from ..operations.decommission import decommission_lpar
from ..operations.ssh_network import (
    get_lpar_memopt_score,
    get_minimum_affinity_policy,
    get_system_memopt_score,
    list_lpar_memopt_scores,
    plan_lpar_memopt_scores,
    plan_system_memopt_score,
    list_resource_group_memopt_scores,
    plan_resource_group_memopt_scores,
)
from ..documents import (
    LparResources,
    PARTITION_TYPES,
    STORAGE_KINDS,
    StorageKind,
    build_lpar_document,
)
from ..ssh.affinity import (
    MemoptLparSelector,
    MemoptResourceGroupSelector,
    validate_memopt_scenario,
)
from ..ssh.lpar import validate_caller_token
from ..ssh.profiles import (
    get_lpar_description,
    get_lpar_msp,
    get_lpar_proc_compat,
    get_proc_compat_modes,
    set_lpar_msp,
    set_lpar_proc_compat,
)


def _memopt_selectors(
    prioritize_name: list[str] | None,
    prioritize_id: list[int] | None,
    exclude_name: list[str] | None,
    exclude_id: list[int] | None,
) -> tuple[MemoptLparSelector | None, MemoptLparSelector | None]:
    """Build shared planning selectors and fail as CLI usage errors."""
    prioritize_name = prioritize_name or []
    prioritize_id = prioritize_id or []
    exclude_name = exclude_name or []
    exclude_id = exclude_id or []
    try:
        prioritized = (
            MemoptLparSelector(tuple(prioritize_name), tuple(prioritize_id))
            if prioritize_name or prioritize_id
            else None
        )
        excluded = (
            MemoptLparSelector(tuple(exclude_name), tuple(exclude_id))
            if exclude_name or exclude_id
            else None
        )
        validate_memopt_scenario(prioritized, excluded)
        return prioritized, excluded
    except ValueError as error:
        _usage_error(str(error))
        raise AssertionError("_usage_error must raise") from error


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


@lpars_app.command("memopt-score")
def lpars_memopt_score(
    lpar_name: str = typer.Argument(..., help="LPAR name or UUID"),
    system_name: str = typer.Argument(..., help="Managed system name or UUID"),
    as_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """Get an LPAR's current memory-optimization affinity score."""
    score = _run(lambda: get_lpar_memopt_score(_ssh_client(), system_name, lpar_name))
    if as_json:
        _print_json(score)
    else:
        console.print(
            f"{score['lpar_name']} (id {score['lpar_id']}): "
            f"curr_lpar_score={score['curr_lpar_score']}"
        )


@lpars_app.command("get-minimum-affinity-policy")
def lpars_get_minimum_affinity_policy(
    lpar_name: str = typer.Argument(..., help="LPAR name or UUID"),
    system_name: str = typer.Argument(..., help="Managed system name or UUID"),
    as_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """Get an LPAR's minimum-affinity policy when supported."""
    policy = _run(
        lambda: get_minimum_affinity_policy(_ssh_client(), system_name, lpar_name)
    )
    if as_json:
        _print_json(asdict(policy))
        return
    if policy.capability == "capability-unavailable":
        console.print(f"unavailable: {policy.unavailable_reason}")
        return
    console.print(
        f"minimum affinity score: {policy.min_affinity_score} "
        f"({policy.min_affinity_score_action})"
    )


@lpars_app.command("memopt-scores")
def lpars_memopt_scores(
    system_name: str = typer.Argument(..., help="Managed system name or UUID"),
    lpar_name: str | None = typer.Option(
        None, "--lpar", help="Filter by LPAR name or UUID"
    ),
    as_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """List current memory-optimization affinity scores for a system's LPARs."""
    scores = _run(
        lambda: list_lpar_memopt_scores(_ssh_client(), system_name, lpar_name)
    )
    if as_json:
        _print_json(scores)
        return
    if not scores:
        console.print("[yellow]No memory-optimization scores reported[/yellow]")
        return
    table = Table(title=f"Memory-optimization scores on {system_name}")
    for column in ("lpar_name", "lpar_id", "curr_lpar_score"):
        table.add_column(column)
    for row in scores:
        table.add_row(
            str(row.get("lpar_name", "")),
            str(row.get("lpar_id", "")),
            str(row.get("curr_lpar_score", "")),
        )
    console.print(table)


@lpars_app.command("system-memopt-score")
def lpars_system_memopt_score(
    system_name: str = typer.Argument(..., help="Managed system name or UUID"),
    as_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """Get a managed system's current memory-optimization affinity score."""
    score = _run(lambda: get_system_memopt_score(_ssh_client(), system_name))
    if as_json:
        _print_json(score)
        return
    console.print(f"current: {score['curr_sys_score']}")


def _run_memopt_plan(
    operation,
    system_name: str,
    prioritize_name: list[str] | None,
    prioritize_id: list[int] | None,
    exclude_name: list[str] | None,
    exclude_id: list[int] | None,
):
    prioritized, excluded = _memopt_selectors(
        prioritize_name, prioritize_id, exclude_name, exclude_id
    )
    return _run(lambda: operation(_ssh_client(), system_name, prioritized, excluded))


def _resource_group_selector(
    names: list[str] | None, ids: list[int] | None, all_groups: bool
) -> MemoptResourceGroupSelector:
    modes = sum((bool(names), bool(ids), all_groups))
    if modes > 1:
        _usage_error(
            "Use only one of --resource-group-name, --resource-group-id, or --all"
        )
    try:
        if names:
            return MemoptResourceGroupSelector(names=tuple(names))
        if ids:
            return MemoptResourceGroupSelector(ids=tuple(ids))
        return MemoptResourceGroupSelector(all=True)
    except ValueError as error:
        _usage_error(str(error))
        raise AssertionError("_usage_error must raise") from error


def _run_resource_group_memopt(
    operation,
    system_name: str,
    names: list[str] | None,
    ids: list[int] | None,
    all_groups: bool,
    as_json: bool,
) -> None:
    selector = _resource_group_selector(names, ids, all_groups)
    result = _run(lambda: operation(_ssh_client(), system_name, selector))
    if as_json:
        _print_json(asdict(result))
        return
    if result.capability == "capability-unavailable":
        console.print(
            f"[yellow]Capability unavailable:[/yellow] {result.unavailable_reason}"
        )
        return
    if not result.items:
        console.print("[yellow]No resource-group affinity scores reported[/yellow]")
        return
    for item in result.items:
        line = (
            f"{item['resource_group_name']} (id {item['resource_group_id']}): "
            f"current: {item['curr_score']}"
        )
        if result.mode == "calculated":
            line += f"; predicted: {item['predicted_score']}; prediction guaranteed: no"
        console.print(line)


@lpars_app.command("resource-group-memopt-scores")
def lpars_resource_group_memopt_scores(
    system_name: str = typer.Argument(..., help="Managed system name or UUID"),
    resource_group_name: list[str] | None = typer.Option(None, "--resource-group-name"),
    resource_group_id: list[int] | None = typer.Option(None, "--resource-group-id"),
    all_groups: bool = typer.Option(False, "--all"),
    as_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """List current resource-group affinity scores when supported."""
    _run_resource_group_memopt(
        list_resource_group_memopt_scores,
        system_name,
        resource_group_name,
        resource_group_id,
        all_groups,
        as_json,
    )


@lpars_app.command("plan-resource-group-memopt-scores")
def lpars_plan_resource_group_memopt_scores(
    system_name: str = typer.Argument(..., help="Managed system name or UUID"),
    resource_group_name: list[str] | None = typer.Option(None, "--resource-group-name"),
    resource_group_id: list[int] | None = typer.Option(None, "--resource-group-id"),
    all_groups: bool = typer.Option(False, "--all"),
    as_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """Calculate potential resource-group affinity scores without running DPO."""
    _run_resource_group_memopt(
        plan_resource_group_memopt_scores,
        system_name,
        resource_group_name,
        resource_group_id,
        all_groups,
        as_json,
    )


@lpars_app.command("plan-memopt-scores")
def lpars_plan_memopt_scores(
    system_name: str = typer.Argument(..., help="Managed system name or UUID"),
    prioritize_name: list[str] | None = typer.Option(None, "--prioritize-name"),
    prioritize_id: list[int] | None = typer.Option(None, "--prioritize-id"),
    exclude_name: list[str] | None = typer.Option(None, "--exclude-name"),
    exclude_id: list[int] | None = typer.Option(None, "--exclude-id"),
    as_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """Predict LPAR affinity scores without applying optimization."""
    scores = _run_memopt_plan(
        plan_lpar_memopt_scores,
        system_name,
        prioritize_name,
        prioritize_id,
        exclude_name,
        exclude_id,
    )
    if as_json:
        _print_json(scores)
        return
    for score in scores:
        console.print(
            f"{score['lpar_name']} (id {score['lpar_id']}): "
            f"current: {score['curr_lpar_score']}; "
            f"predicted: {score['predicted_lpar_score']}; "
            "prediction guaranteed: no"
        )


@lpars_app.command("plan-system-memopt-score")
def lpars_plan_system_memopt_score(
    system_name: str = typer.Argument(..., help="Managed system name or UUID"),
    prioritize_name: list[str] | None = typer.Option(None, "--prioritize-name"),
    prioritize_id: list[int] | None = typer.Option(None, "--prioritize-id"),
    exclude_name: list[str] | None = typer.Option(None, "--exclude-name"),
    exclude_id: list[int] | None = typer.Option(None, "--exclude-id"),
    as_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """Predict system affinity score without applying optimization."""
    score = _run_memopt_plan(
        plan_system_memopt_score,
        system_name,
        prioritize_name,
        prioritize_id,
        exclude_name,
        exclude_id,
    )
    if as_json:
        _print_json(score)
        return
    console.print(
        f"current: {score['curr_sys_score']}; "
        f"predicted: {score['predicted_sys_score']}; "
        "prediction guaranteed: no"
    )


@lpars_app.command("power-on")
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


@lpars_app.command("power-off")
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


@lpars_app.command("create")
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
            await prevalidate_lpar_pcie_assignments(hmc, system, assignments)
            creation = await create_and_stamp_lpar(
                hmc,
                system,
                LparCreation(
                    name,
                    partition_type,
                    resources,
                    partition_id=partition_id,
                    caller_token=caller_token,
                ),
            )
            if creation.lpar is None:
                return creation, None
            outcome = await _apply_validated_lpar_pcie_assignments(
                hmc, system, name, assignments
            )
            return creation, outcome

    result, assignment_result = _run(_go)

    console.print(f"[green]Created LPAR '{name}'[/green]")
    _print_json(result.lpar)
    for warning in result.warnings:
        err_console.print(f"[yellow]Warning: {warning}[/yellow]")
    if assignment_result is not None and assignment_result.steps:
        _print_json(asdict(assignment_result))
        if not assignment_result.workflow_completed:
            raise typer.Exit(1)


@lpars_app.command("modify")
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
    has_resource_changes = any(
        value is not None
        for value in (
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

    async def _go():
        async with _client() as hmc:
            if system is not None:
                await prevalidate_lpar_pcie_assignments(hmc, system, assignments)
            if not yes:
                if not typer.confirm(f"Apply changes to '{name_or_uuid}'?"):
                    raise typer.Abort()
            if new_name is not None:
                uuid, updated = await rename_lpar(
                    hmc,
                    cast(str, system),
                    name_or_uuid,
                    new_name,
                    ownership_override=ownership_override,
                )
                if not has_resource_changes and assignments == LparPcieAssignments():
                    return uuid, updated
                selector = uuid
            else:
                selector = name_or_uuid
            uuid = await _resolve_partition_uuid(hmc, selector)
            if uuid is None:
                return None, None
            xml = build_lpar_document(name=None, resources=resources)
            updated = (
                await hmc.modify_logical_partition(uuid, xml)
                if has_resource_changes
                else None
            )
            assignment_result = await _apply_validated_lpar_pcie_assignments(
                hmc,
                cast(str, system),
                selector,
                assignments,
                ownership_override=ownership_override,
            )
            return uuid, {
                "resources": updated,
                "assignments": asdict(assignment_result),
            }

    uuid, updated = _run(_go)

    if uuid is None:
        _partition_not_found(name_or_uuid)
    console.print(f"[green]Modified LPAR {uuid}[/green]")
    _print_json(updated)


@lpars_app.command("delete")
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


@lpars_app.command("decommission")
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
            status = step.get("status", "-")
            style = (
                "green"
                if status == "ok"
                else ("yellow" if status in ("dry_run", "skipped") else "red")
            )
            table.add_row(
                step.get("step", "-"),
                f"[{style}]{status}[/{style}]",
                "-" if "result" not in step else str(step["result"]),
            )
        console.print(table)

        if result.warnings:
            for warning in result.warnings:
                console.print(f"[yellow]Warning: {warning}[/yellow]")

    if not result.dry_run and not result.workflow_completed:
        raise typer.Exit(1)


@lpars_app.command("get-description")
def lpars_get_description(
    lpar_name: str = typer.Argument(..., help="LPAR name"),
    system_name: str = typer.Argument(..., help="Managed system name"),
) -> None:
    """Get the description field of an LPAR (HMC CLI via SSH)."""
    result = _run(lambda: get_lpar_description(_ssh_config(), system_name, lpar_name))

    console.print(result.strip() or "(no description set)")


@lpars_app.command("set-description")
def lpars_set_description(
    lpar_name: str = typer.Argument(..., help="LPAR name"),
    system_name: str = typer.Argument(..., help="Managed system name"),
    description: str = typer.Argument(
        ...,
        help="New description text (printable ASCII, no HMC attribute-record "
        "structure; the error names the character)",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    ownership_override: bool = typer.Option(
        False,
        "--ownership-override",
        help="Bypass ownership protection after operator approval",
    ),
) -> None:
    """Set the description field of an LPAR (HMC CLI via SSH)."""
    if not yes and not typer.confirm(
        f"Set description on '{lpar_name}' (system {system_name})?"
    ):
        raise typer.Abort()

    async def _go():
        async with _client() as hmc:
            return await set_lpar_ownership_description(
                hmc,
                system_name,
                lpar_name,
                description,
                ownership_override=ownership_override,
            )

    result = _run(_go)

    console.print(f"[green]Description updated for '{lpar_name}'[/green]")
    if result.strip():
        console.print(result.strip())


@lpars_app.command("get-msp")
def lpars_get_msp(
    lpar_name: str = typer.Argument(..., help="LPAR name"),
    system_name: str = typer.Argument(..., help="Managed system name"),
) -> None:
    """Get the MSP (Migratable Service Partition) flag of an LPAR (HMC CLI via SSH)."""
    enabled = _run(lambda: get_lpar_msp(_ssh_config(), system_name, lpar_name))

    console.print("enabled" if enabled else "disabled")


@lpars_app.command("set-msp")
def lpars_set_msp(
    lpar_name: str = typer.Argument(..., help="LPAR name"),
    system_name: str = typer.Argument(..., help="Managed system name"),
    enabled: bool = typer.Argument(..., help="True to enable MSP, False to disable"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Set the MSP (Migratable Service Partition) flag of an LPAR (HMC CLI via SSH)."""
    if not yes and not typer.confirm(
        f"Set MSP={'1' if enabled else '0'} on '{lpar_name}' (system {system_name})?"
    ):
        raise typer.Abort()
    result = _run(lambda: set_lpar_msp(_ssh_config(), system_name, lpar_name, enabled))

    console.print(f"[green]MSP updated for '{lpar_name}'[/green]")
    if result.strip():
        console.print(result.strip())


@lpars_app.command("get-proc-compat-modes")
def lpars_get_proc_compat_modes(
    system_name: str = typer.Argument(..., help="Managed system name"),
) -> None:
    """Get processor compatibility modes supported by a managed system (HMC CLI via SSH)."""
    modes = _run(lambda: get_proc_compat_modes(_ssh_config(), system_name))

    console.print(",".join(modes) or "(no modes returned)")


@lpars_app.command("get-proc-compat")
def lpars_get_proc_compat(
    lpar_name: str = typer.Argument(..., help="LPAR name"),
    system_name: str = typer.Argument(..., help="Managed system name"),
    as_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """Get the current and pending processor compatibility modes for an LPAR (HMC CLI via SSH)."""
    info = _run(lambda: get_lpar_proc_compat(_ssh_config(), system_name, lpar_name))

    desired = info["desired"]
    curr = info["curr"]

    if as_json:
        _print_json(info)
    else:
        table = Table(title=f"Processor Compatibility Mode: {lpar_name}")
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="green")
        table.add_row("Desired Mode", desired or "-")
        table.add_row("Current Mode", curr or "-")
        console.print(table)


@lpars_app.command("set-proc-compat")
def lpars_set_proc_compat(
    lpar_name: str = typer.Argument(..., help="LPAR name"),
    system_name: str = typer.Argument(..., help="Managed system name"),
    mode: ProcessorCompatibilityMode = typer.Argument(
        ..., help="Processor compatibility mode supported by the managed system"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Set the processor compatibility mode of an LPAR (HMC CLI via SSH)."""
    if not yes and not typer.confirm(
        f"Set processor compatibility mode to '{mode}' on LPAR '{lpar_name}' (system {system_name})?"
    ):
        raise typer.Abort()
    result = _run(
        lambda: set_lpar_proc_compat(_ssh_config(), system_name, lpar_name, mode)
    )

    console.print(
        f"[green]Processor compatibility mode updated for '{lpar_name}'[/green]"
    )
    if result.strip():
        console.print(result.strip())


@lpars_app.command("provision")
def lpars_provision(
    system: str = typer.Option(
        ..., "--system", "-s", help="Target managed system name or UUID"
    ),
    name: str = typer.Option(..., "--name", "-n", help="Name for the new LPAR"),
    port_vlan_id: int = typer.Option(
        ..., "--vlan", help="Port VLAN ID for the network adapter"
    ),
    vios_uuid: str = typer.Option(
        ..., "--vios-uuid", help="UUID of the VIOS for vSCSI / storage"
    ),
    vios_partition_id: int = typer.Option(
        ..., "--vios-partition-id", help="Numeric partition ID of the VIOS"
    ),
    vios_slot: int = typer.Option(
        ..., "--vios-slot", help="Virtual slot number of the VIOS server adapter"
    ),
    storage_name: str = typer.Option(
        ..., "--storage-name", help="VirtualDisk or PhysicalVolume name to map"
    ),
    partition_type: str = typer.Option(
        "AIX/Linux", "--type", help=f"Partition type: {', '.join(PARTITION_TYPES)}"
    ),
    min_memory: int = typer.Option(256, "--min-mem", help="Minimum memory (MiB)"),
    memory: int = typer.Option(4096, "--mem", help="Desired memory (MiB)"),
    max_memory: int = typer.Option(8192, "--max-mem", help="Maximum memory (MiB)"),
    vcpus: int = typer.Option(1, "--vcpus", help="Desired virtual CPUs"),
    max_vcpus: int = typer.Option(2, "--max-vcpus", help="Maximum virtual CPUs"),
    storage_kind: str = typer.Option(
        "VirtualDisk", "--storage-kind", help='"VirtualDisk" or "PhysicalVolume"'
    ),
    vg_uuid: str | None = typer.Option(
        None, "--vg-uuid", help="Volume group UUID to validate (optional)"
    ),
    power_on: bool = typer.Option(
        True, "--power-on/--no-power-on", help="Power on after provisioning"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Validate preconditions only; do not create"
    ),
    pcie_assignments: Path | None = typer.Option(
        None,
        "--pcie-assignments",
        help="JSON file using the declarative LparPcieAssignments schema",
    ),
    as_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Provision a new LPAR end-to-end: create, add network adapter, add vSCSI adapter, map storage, power on.

    Always validates preconditions first (name uniqueness, VLAN existence, volume-group existence).
    Pass --dry-run to run precondition checks only without creating anything.
    On partial failure the completed steps are reported as "ok", the failed step as "error",
    and remaining steps as "skipped". No automatic rollback is performed.
    """
    from ..operations.provision import (
        ProvisionNetwork,
        ProvisionStorage,
        provision_lpar,
    )

    assignments = _load_pcie_assignments(pcie_assignments)

    if partition_type not in PARTITION_TYPES:
        _usage_error(
            f"--type must be one of {', '.join(PARTITION_TYPES)}, got {partition_type!r}"
        )
    if storage_kind not in STORAGE_KINDS:
        _usage_error(
            "--storage-kind must be one of "
            f"{', '.join(sorted(STORAGE_KINDS))}, got {storage_kind!r}"
        )
    if not dry_run and not yes:
        typer.confirm(
            f"Provision LPAR '{name}' on system '{system}' (VLAN {port_vlan_id}, VIOS {vios_uuid})?"
            + (" [DRY RUN]" if dry_run else ""),
            abort=True,
        )

    async def _go():
        async with _client() as hmc:
            return await provision_lpar(
                hmc,
                system_name_or_uuid=system,
                name=name,
                network=ProvisionNetwork(port_vlan_id, vios_partition_id, vios_slot),
                storage=ProvisionStorage(
                    vios_uuid,
                    storage_name,
                    cast(StorageKind, storage_kind),
                    vg_uuid,
                ),
                resources=LparResources(
                    min_memory=min_memory,
                    desired_memory=memory,
                    max_memory=max_memory,
                    desired_vcpus=vcpus,
                    max_vcpus=max_vcpus,
                ),
                partition_type=partition_type,
                power_on=power_on,
                dry_run=dry_run,
                assignments=assignments,
            )

    result = _run(_go)

    if as_json:
        from dataclasses import asdict

        _print_json(asdict(result))
        return

    if dry_run:
        console.print(
            "[yellow]DRY RUN — preconditions validated, no LPAR created[/yellow]"
        )
    elif result.workflow_completed:
        console.print(f"[green]LPAR '{name}' provisioned successfully[/green]")
    elif result.resource_created:
        identity = result.lpar_uuid or "UUID unavailable"
        console.print(
            f"[yellow]LPAR '{name}' was created ({identity}), but provisioning "
            "is incomplete — check step results[/yellow]"
        )
    else:
        console.print(
            f"[yellow]LPAR '{name}' was not created — check step results[/yellow]"
        )

    table = Table(title=f"Provision steps: {name}")
    table.add_column("Step", style="cyan")
    table.add_column("Status", style="green")
    for step in result.steps:
        status = step.get("status", "-")
        style = (
            "green"
            if status == "ok"
            else ("yellow" if status in ("dry_run", "skipped") else "red")
        )
        table.add_row(step.get("step", "-"), f"[{style}]{status}[/{style}]")
    console.print(table)

    if result.warnings:
        for w in result.warnings:
            console.print(f"[yellow]Warning: {w}[/yellow]")


# ====================================================================== #
# LPAR Boot Order Commands
# ====================================================================== #


@lpars_app.command("read-boot-order")
def lpars_read_boot_order(
    system_name: str = typer.Argument(..., help="Managed system name"),
    lpar_uuid: str = typer.Argument(..., help="Logical partition UUID"),
) -> None:
    """Read an LPAR's boot order state (pending and current).

    Returns the boot device order for the LPAR, including both the pending
    boot string (next boot) and the current boot device list.

    Example:
        lpars read-boot-order system1 aaaa0000-0000-0000-0000-000000000001
    """
    from ..operations.lpar import read_lpar_boot_order

    result = _with_client(
        lambda hmc: read_lpar_boot_order(
            hmc,
            system_name_or_uuid=system_name,
            lpar_uuid=lpar_uuid,
        )
    )

    _print_json(result)


@lpars_app.command("set-boot-order")
def lpars_set_boot_order(
    system_name: str = typer.Argument(..., help="Managed system name"),
    lpar_uuid: str = typer.Argument(..., help="Logical partition UUID"),
    devices: str = typer.Argument(
        ..., help="Ordered boot device list (comma-separated: cd,disk,network)"
    ),
    *,
    ownership_override: bool = typer.Option(
        False, "--ownership-override", help="Skip ownership token validation"
    ),
) -> None:
    """Set an LPAR's boot order to a validated device selector list.

    Sets the PendingBootString to an ordered list of boot device selectors.
    Changes take effect on the next LPAR activation (no reboot required).

    Args:
        system_name: Managed system name.
        lpar_uuid: UUID of the logical partition.
        devices: Ordered list of boot device selectors (cd, disk, network),
                 comma-separated. The first device is tried first, then the second, etc.
        ownership_override: If True, skip ownership token validation.

    Example:
        lpars set-boot-order system1 lpar-uuid-123 "network,cd,disk"
    """
    from ..documents import BOOT_DEVICE_SELECTORS
    from ..operations.lpar import set_lpar_boot_order

    # Parse and validate device list
    device_list = [d.strip() for d in devices.split(",") if d.strip()]

    for device in device_list:
        if device not in BOOT_DEVICE_SELECTORS:
            raise typer.BadParameter(
                f"Invalid boot device selector: {device!r}. "
                f"Must be one of: {', '.join(BOOT_DEVICE_SELECTORS)}"
            )

    if not device_list:
        raise typer.BadParameter("Boot order must contain at least one device")

    result = _with_client(
        lambda hmc: set_lpar_boot_order(
            hmc,
            system_name_or_uuid=system_name,
            lpar_uuid=lpar_uuid,
            devices=device_list,
            ownership_override=ownership_override,
        )
    )

    console.print(f"[green]Boot order set to: {', '.join(device_list)}[/green]")
    _print_json(result)


@lpars_app.command("clear-boot-order")
def lpars_clear_boot_order(
    system_name: str = typer.Argument(..., help="Managed system name"),
    lpar_uuid: str = typer.Argument(..., help="Logical partition UUID"),
    *,
    ownership_override: bool = typer.Option(
        False, "--ownership-override", help="Skip ownership token validation"
    ),
) -> None:
    """Clear an LPAR's boot order (restore HMC defaults).

    Clears the PendingBootString, restoring the default boot behavior.
    Changes take effect on the next LPAR activation (no reboot required).

    Example:
        lpars clear-boot-order system1 aaaa0000-0000-0000-0000-000000000001
    """
    from ..operations.lpar import clear_lpar_boot_order

    result = _with_client(
        lambda hmc: clear_lpar_boot_order(
            hmc,
            system_name_or_uuid=system_name,
            lpar_uuid=lpar_uuid,
            ownership_override=ownership_override,
        )
    )

    console.print("[green]Boot order cleared (restored defaults)[/green]")
    _print_json(result)
