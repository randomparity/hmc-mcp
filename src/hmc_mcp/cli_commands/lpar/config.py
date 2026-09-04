"""CLI commands for LPAR configuration and affinity."""

from __future__ import annotations

from dataclasses import asdict
from typing import cast

import typer
from rich.table import Table

from hmc_mcp.client.core import HMCClient
from hmc_mcp.operations.ownership import set_lpar_ownership_description

from ...operations.affinity.ssh import (
    get_lpar_memopt_score,
    get_minimum_affinity_policy,
    get_system_memopt_score,
    list_lpar_memopt_scores,
    list_resource_group_memopt_scores,
    plan_lpar_memopt_scores,
    plan_resource_group_memopt_scores,
    plan_system_memopt_score,
)
from ...operations.lpar.core import (
    ProcessorCompatibilityMode,
)
from ...ssh.affinity import (
    MemoptLparSelector,
    MemoptResourceGroupSelector,
    validate_memopt_scenario,
)
from ...ssh.profiles import (
    get_lpar_description,
    get_lpar_msp,
    get_lpar_proc_compat,
    get_proc_compat_modes,
    set_lpar_msp,
    set_lpar_proc_compat,
)
from ..output import console, print_json, usage_error
from ..runtime import client, run, ssh_config


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
        usage_error(str(error))
        raise AssertionError("usage_error must raise") from error


def lpars_memopt_score(
    lpar_name: str = typer.Argument(..., help="LPAR name or UUID"),
    system_name: str = typer.Argument(..., help="Managed system name or UUID"),
    as_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """Get an LPAR's current memory-optimization affinity score."""
    score = run(lambda: get_lpar_memopt_score(cast(HMCClient, ssh_config()), system_name, lpar_name))
    if as_json:
        print_json(score)
    else:
        console.print(
            f"{score['lpar_name']} (id {score['lpar_id']}): "
            f"curr_lpar_score={score['curr_lpar_score']}"
        )


def lpars_get_minimum_affinity_policy(
    lpar_name: str = typer.Argument(..., help="LPAR name or UUID"),
    system_name: str = typer.Argument(..., help="Managed system name or UUID"),
    as_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """Get an LPAR's minimum-affinity policy when supported."""
    policy = run(
        lambda: get_minimum_affinity_policy(cast(HMCClient, ssh_config()), system_name, lpar_name)
    )
    if as_json:
        print_json(asdict(policy))
        return
    if policy.capability == "capability-unavailable":
        console.print(f"unavailable: {policy.unavailable_reason}")
        return
    console.print(
        f"minimum affinity score: {policy.min_affinity_score} "
        f"({policy.min_affinity_score_action})"
    )


def lpars_memopt_scores(
    system_name: str = typer.Argument(..., help="Managed system name or UUID"),
    lpar_name: str | None = typer.Option(
        None, "--lpar", help="Filter by LPAR name or UUID"
    ),
    as_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """List current memory-optimization affinity scores for a system's LPARs."""
    scores = run(
        lambda: list_lpar_memopt_scores(cast(HMCClient, ssh_config()), system_name, lpar_name)
    )
    if as_json:
        print_json(scores)
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


def lpars_system_memopt_score(
    system_name: str = typer.Argument(..., help="Managed system name or UUID"),
    as_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """Get a managed system's current memory-optimization affinity score."""
    score = run(lambda: get_system_memopt_score(cast(HMCClient, ssh_config()), system_name))
    if as_json:
        print_json(score)
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
    return run(lambda: operation(cast(HMCClient, ssh_config()), system_name, prioritized, excluded))


def _resource_group_selector(
    names: list[str] | None, ids: list[int] | None, all_groups: bool
) -> MemoptResourceGroupSelector:
    modes = sum((bool(names), bool(ids), all_groups))
    if modes > 1:
        usage_error(
            "Use only one of --resource-group-name, --resource-group-id, or --all"
        )
    try:
        if names:
            return MemoptResourceGroupSelector(names=tuple(names))
        if ids:
            return MemoptResourceGroupSelector(ids=tuple(ids))
        return MemoptResourceGroupSelector(all=True)
    except ValueError as error:
        usage_error(str(error))
        raise AssertionError("usage_error must raise") from error


def _run_resource_group_memopt(
    operation,
    system_name: str,
    names: list[str] | None,
    ids: list[int] | None,
    all_groups: bool,
    as_json: bool,
) -> None:
    selector = _resource_group_selector(names, ids, all_groups)
    result = run(lambda: operation(cast(HMCClient, ssh_config()), system_name, selector))
    if as_json:
        print_json(asdict(result))
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
        print_json(scores)
        return
    for score in scores:
        console.print(
            f"{score['lpar_name']} (id {score['lpar_id']}): "
            f"current: {score['curr_lpar_score']}; "
            f"predicted: {score['predicted_lpar_score']}; "
            "prediction guaranteed: no"
        )


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
        print_json(score)
        return
    console.print(
        f"current: {score['curr_sys_score']}; "
        f"predicted: {score['predicted_sys_score']}; "
        "prediction guaranteed: no"
    )


def lpars_get_description(
    lpar_name: str = typer.Argument(..., help="LPAR name"),
    system_name: str = typer.Argument(..., help="Managed system name"),
) -> None:
    """Get the description field of an LPAR (HMC CLI via SSH)."""
    result = run(lambda: get_lpar_description(ssh_config(), system_name, lpar_name))

    console.print(result.strip() or "(no description set)")


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
        async with client() as hmc:
            return await set_lpar_ownership_description(
                hmc,
                system_name,
                lpar_name,
                description,
                ownership_override=ownership_override,
            )

    result = run(_go)

    console.print(f"[green]Description updated for '{lpar_name}'[/green]")
    if result.strip():
        console.print(result.strip())


def lpars_get_msp(
    lpar_name: str = typer.Argument(..., help="LPAR name"),
    system_name: str = typer.Argument(..., help="Managed system name"),
) -> None:
    """Get the MSP (Migratable Service Partition) flag of an LPAR (HMC CLI via SSH)."""
    enabled = run(lambda: get_lpar_msp(ssh_config(), system_name, lpar_name))

    console.print("enabled" if enabled else "disabled")


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
    result = run(lambda: set_lpar_msp(ssh_config(), system_name, lpar_name, enabled))

    console.print(f"[green]MSP updated for '{lpar_name}'[/green]")
    if result.strip():
        console.print(result.strip())


def lpars_get_proc_compat_modes(
    system_name: str = typer.Argument(..., help="Managed system name"),
) -> None:
    """Get processor compatibility modes supported by a managed system (HMC CLI via SSH)."""
    modes = run(lambda: get_proc_compat_modes(ssh_config(), system_name))

    console.print(",".join(modes) or "(no modes returned)")


def lpars_get_proc_compat(
    lpar_name: str = typer.Argument(..., help="LPAR name"),
    system_name: str = typer.Argument(..., help="Managed system name"),
    as_json: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """Get the current and pending processor compatibility modes for an LPAR (HMC CLI via SSH)."""
    info = run(lambda: get_lpar_proc_compat(ssh_config(), system_name, lpar_name))

    desired = info["desired"]
    curr = info["curr"]

    if as_json:
        print_json(info)
    else:
        table = Table(title=f"Processor Compatibility Mode: {lpar_name}")
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="green")
        table.add_row("Desired Mode", desired or "-")
        table.add_row("Current Mode", curr or "-")
        console.print(table)


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
    result = run(
        lambda: set_lpar_proc_compat(ssh_config(), system_name, lpar_name, mode)
    )

    console.print(
        f"[green]Processor compatibility mode updated for '{lpar_name}'[/green]"
    )
    if result.strip():
        console.print(result.strip())


def register_commands(group: typer.Typer) -> None:
    """Register this module’s commands on *group*."""
    group.command("memopt-score")(lpars_memopt_score)
    group.command("get-minimum-affinity-policy")(lpars_get_minimum_affinity_policy)
    group.command("memopt-scores")(lpars_memopt_scores)
    group.command("system-memopt-score")(lpars_system_memopt_score)
    group.command("resource-group-memopt-scores")(lpars_resource_group_memopt_scores)
    group.command("plan-resource-group-memopt-scores")(
        lpars_plan_resource_group_memopt_scores
    )
    group.command("plan-memopt-scores")(lpars_plan_memopt_scores)
    group.command("plan-system-memopt-score")(lpars_plan_system_memopt_score)
    group.command("get-description")(lpars_get_description)
    group.command("set-description")(lpars_set_description)
    group.command("get-msp")(lpars_get_msp)
    group.command("set-msp")(lpars_set_msp)
    group.command("get-proc-compat-modes")(lpars_get_proc_compat_modes)
    group.command("get-proc-compat")(lpars_get_proc_compat)
    group.command("set-proc-compat")(lpars_set_proc_compat)
