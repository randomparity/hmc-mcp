"""CLI commands for LPARs: list/show/state, power, LPM, lifecycle, description, MSP, and processor compatibility.
"""

from __future__ import annotations

import typer
from rich.table import Table

from .common import is_uuid

from .cli_app import (
    _client,
    _first_field,
    _output,
    _print_json,
    _resolve_partition_uuid,
    _run,
    _ssh_config,
    _with_client,
    console,
    err_console,
    lpars_app,
)

from .jobs import power_off_lpar_job, power_on_lpar_job
from .documents import LparResources, PARTITION_TYPES, build_lpar_document
from .ssh import (
    get_lpar_description,
    get_lpar_msp,
    get_lpar_proc_compat,
    get_proc_compat_modes,
    set_lpar_description,
    set_lpar_msp,
    set_lpar_proc_compat,
)



@lpars_app.command("list")
def lpars_list(
    system: str | None = typer.Option(None, "--system", "-s", help="Restrict to this managed system UUID"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List logical partitions."""

    lpars = _with_client(lambda hmc: hmc.list_logical_partitions(system))

    table = None
    if not as_json:
        table = Table(title="Logical Partitions")
        for col in ("Name", "ID", "UUID", "State", "Type", "OS", "RMC"):
            table.add_column(col)
        for lpar in lpars:
            table.add_row(
                _first_field(lpar, "PartitionName"),
                _first_field(lpar, "PartitionID"),
                lpar.get("UUID") or "-",
                _first_field(lpar, "PartitionState"),
                _first_field(lpar, "PartitionType"),
                _first_field(lpar, "OperatingSystemVersion", default="-"),
                _first_field(lpar, "ResourceMonitoringControlState", "RMCState"),
            )
    _output(lpars, as_json, table, "No logical partitions found")


@lpars_app.command("show")
def lpars_show(
    name_or_uuid: str = typer.Argument(..., help="Partition name or UUID"),
    as_json: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    """Show one LPAR, looked up by name (exact) or by UUID."""

    lpar = _with_client(
        lambda hmc: hmc.get_logical_partition(name_or_uuid)
        if is_uuid(name_or_uuid)
        else hmc.find_partition_by_name(name_or_uuid)
    )

    if lpar is None:
        err_console.print(f"[yellow]Partition '{name_or_uuid}' not found[/yellow]")
        raise typer.Exit(code=1)
    _print_json(lpar)


@lpars_app.command("state")
def lpars_state(name_or_uuid: str = typer.Argument(..., help="Partition name or UUID")) -> None:
    """Print just the current state of an LPAR."""

    async def _go():
        async with _client() as hmc:
            uuid = await _resolve_partition_uuid(hmc, name_or_uuid)
            if uuid is None:
                return None
            return await hmc.get_quick_property("LogicalPartition", uuid, "PartitionState")

    state = _run(_go)

    if state is None:
        err_console.print(f"[yellow]Partition '{name_or_uuid}' not found[/yellow]")
        raise typer.Exit(code=1)
    console.print(state)


@lpars_app.command("power-on")
def lpars_power_on(
    name_or_uuid: str = typer.Argument(..., help="Partition name or UUID"),
    wait: bool = typer.Option(False, "--wait/--no-wait", help="Wait for job completion"),
    timeout: int = typer.Option(300, "--timeout", help="Seconds to wait (with --wait)"),
    interval: int = typer.Option(5, "--interval", help="Poll interval seconds (with --wait)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Power on an LPAR (submits a PowerOn job)."""
    _power_lpar(name_or_uuid, on=True, yes=yes, wait=wait, timeout=timeout, interval=interval)


@lpars_app.command("power-off")
def lpars_power_off(
    name_or_uuid: str = typer.Argument(..., help="Partition name or UUID"),
    immediate: bool = typer.Option(False, "--immediate", help="Immediate power off (no graceful shutdown)"),
    wait: bool = typer.Option(False, "--wait/--no-wait", help="Wait for job completion"),
    timeout: int = typer.Option(300, "--timeout", help="Seconds to wait (with --wait)"),
    interval: int = typer.Option(5, "--interval", help="Poll interval seconds (with --wait)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Power off an LPAR (submits a PowerOff job)."""
    _power_lpar(name_or_uuid, on=False, immediate=immediate, yes=yes, wait=wait, timeout=timeout, interval=interval)




def _lpm_run(name_or_uuid: str, fn, action: str, target: str | None, yes: bool) -> None:
    """Shared resolve -> confirm -> run helper for LPM operations."""

    async def _go():
        async with _client() as hmc:
            uuid = await _resolve_partition_uuid(hmc, name_or_uuid)
            if uuid is None:
                return None, None
            if not yes:
                dest = f" to '{target}'" if target else ""
                if not typer.confirm(f"Really {action} partition '{name_or_uuid}' ({uuid}){dest}?"):
                    raise typer.Abort()
            return uuid, await fn(hmc, uuid)

    uuid, job = _run(_go)

    if uuid is None:
        err_console.print(f"[yellow]Partition '{name_or_uuid}' not found[/yellow]")
        raise typer.Exit(code=1)
    console.print(f"[green]Submitted {action} for {uuid}[/green]")
    _print_json(job)


@lpars_app.command("migrate")
def lpars_migrate(
    name_or_uuid: str = typer.Argument(..., help="Partition name or UUID"),
    target: str = typer.Option(..., "--target", help="Target managed system name"),
    profile: str | None = typer.Option(None, "--profile", help="Target profile name"),
    wait_time: int | None = typer.Option(None, "--wait-time", help="Override operation wait time"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Live-migrate (LPM) an LPAR to another managed system."""

    async def _fn(hmc, uuid):
        return await hmc.lpar_migrate(uuid, target, profile, wait_time=wait_time)

    _lpm_run(name_or_uuid, _fn, "Migrate", target, yes)


@lpars_app.command("migrate-validate")
def lpars_migrate_validate(
    name_or_uuid: str = typer.Argument(..., help="Partition name or UUID"),
    target: str = typer.Option(..., "--target", help="Target managed system name"),
    profile: str | None = typer.Option(None, "--profile", help="Target profile name"),
    wait_time: int | None = typer.Option(None, "--wait-time"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Validate whether an LPM migration would succeed."""

    async def _fn(hmc, uuid):
        return await hmc.lpar_migrate_validate(uuid, target, profile, wait_time=wait_time)

    _lpm_run(name_or_uuid, _fn, "MigrateValidate", target, yes)


@lpars_app.command("migrate-abort")
def lpars_migrate_abort(
    name_or_uuid: str = typer.Argument(..., help="Partition name or UUID"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Abort an in-progress LPM migration."""

    async def _fn(hmc, uuid):
        return await hmc.lpar_migrate_abort(uuid)

    _lpm_run(name_or_uuid, _fn, "MigrateAbort", None, yes)


@lpars_app.command("migrate-recover")
def lpars_migrate_recover(
    name_or_uuid: str = typer.Argument(..., help="Partition name or UUID"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Recover an LPAR after a failed LPM migration."""

    async def _fn(hmc, uuid):
        return await hmc.lpar_migrate_recover(uuid)

    _lpm_run(name_or_uuid, _fn, "MigrateRecover", None, yes)


@lpars_app.command("remote-restart")
def lpars_remote_restart(
    name_or_uuid: str = typer.Argument(..., help="Partition name or UUID"),
    target: str = typer.Option(..., "--target", help="Target managed system name"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Remote-restart a failed LPAR on another managed system."""

    async def _fn(hmc, uuid):
        return await hmc.lpar_remote_restart(uuid, target)

    _lpm_run(name_or_uuid, _fn, "RemoteRestart", target, yes)



def _power_lpar(
    name_or_uuid: str,
    on: bool,
    immediate: bool = False,
    yes: bool = False,
    wait: bool = False,
    timeout: int = 300,
    interval: int = 5,
) -> None:
    async def _go():
        async with _client() as hmc:
            uuid = await _resolve_partition_uuid(hmc, name_or_uuid)
            if uuid is None:
                return None, None
            if not yes:
                op = "PowerOn" if on else ("Immediate PowerOff" if immediate else "PowerOff")
                name = name_or_uuid
                if not is_uuid(name_or_uuid):
                    found = await hmc.get_logical_partition(uuid)
                    if found:
                        name = _first_field(found, "PartitionName", default=name_or_uuid)
                if not typer.confirm(f"Really submit {op} for partition '{name}' ({uuid})?"):
                    err_console.print("Aborted.")
                    raise typer.Abort()
            if on:
                job = await hmc.submit_job(
                    f"/rest/api/uom/LogicalPartition/{uuid}/do/PowerOn", power_on_lpar_job()
                )
            else:
                job = await hmc.submit_job(
                    f"/rest/api/uom/LogicalPartition/{uuid}/do/PowerOff",
                    power_off_lpar_job(immediate=immediate),
                )
            if wait and job is not None:
                job_uuid = job.get("UUID") or (job.get("Resource") or {}).get("JobID")
                if job_uuid:
                    job = await hmc.wait_for_job(job_uuid, timeout, interval)
            return uuid, job

    uuid, job = _run(_go)

    if uuid is None:
        err_console.print(f"[yellow]Partition '{name_or_uuid}' not found[/yellow]")
        raise typer.Exit(code=1)
    console.print(f"[green]Job submitted[/green] for {uuid}")
    _print_json(job)




@lpars_app.command("create")
def lpars_create(
    name: str = typer.Argument(..., help="Name for the new partition"),
    system: str = typer.Option(..., "--system", "-s", help="Target managed system UUID"),
    partition_type: str = typer.Option("AIX/Linux", "--type", help=f"One of: {', '.join(PARTITION_TYPES)}"),
    partition_id: int | None = typer.Option(None, "--id", help="Partition ID (auto-assigned if omitted)"),
    min_memory: int = typer.Option(256, "--min-mem", help="Minimum memory (MiB)"),
    memory: int = typer.Option(4096, "--mem", help="Desired memory (MiB)"),
    max_memory: int = typer.Option(8192, "--max-mem", help="Maximum memory (MiB)"),
    dedicated: bool = typer.Option(False, "--dedicated", help="Dedicated CPUs instead of shared"),
    min_procs: float | None = typer.Option(None, "--min-procs", help="Min processing units / dedicated CPUs"),
    procs: float | None = typer.Option(None, "--procs", help="Desired processing units / dedicated CPUs"),
    max_procs: float | None = typer.Option(None, "--max-procs", help="Max processing units / dedicated CPUs"),
    min_vcpus: int | None = typer.Option(None, "--min-vcpus", help="Min virtual processors (shared)"),
    vcpus: int | None = typer.Option(1, "--vcpus", help="Desired virtual processors (shared)"),
    max_vcpus: int | None = typer.Option(2, "--max-vcpus", help="Max virtual processors (shared)"),
    capped: bool = typer.Option(False, "--capped", help="Cap shared CPU (default uncapped)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Create a new LPAR on a managed system.

    Creates the partition powered off with a default profile; storage/network
    and boot settings are configured afterwards via the HMC.
    """
    if not yes:
        typer.confirm(
            f"Create LPAR '{name}' ({partition_type}, {memory} MiB) on system {system}?",
            abort=True,
        )
    xml = build_lpar_document(
        name=name,
        partition_type=partition_type,
        partition_id=partition_id,
        resources=LparResources(
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
        ),
    )

    created = _with_client(lambda hmc: hmc.create_logical_partition(system, xml))

    console.print(f"[green]Created LPAR '{name}'[/green]")
    _print_json(created)


@lpars_app.command("modify")
def lpars_modify(
    name_or_uuid: str = typer.Argument(..., help="Partition name or UUID"),
    new_name: str | None = typer.Option(None, "--name", help="Rename the partition"),
    min_memory: int | None = typer.Option(None, "--min-mem", help="Minimum memory (MiB)"),
    memory: int | None = typer.Option(None, "--mem", help="Desired memory (MiB)"),
    max_memory: int | None = typer.Option(None, "--max-mem", help="Maximum memory (MiB)"),
    dedicated: bool | None = typer.Option(
        None, "--dedicated/--no-dedicated", help="Assign dedicated CPUs (default: leave unchanged)"
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
) -> None:
    """Change an LPAR's name and/or resource assignment (memory / CPU).

    Only options you pass are changed. On a running partition these are
    dynamic (DLPAR) operations and need RMC up; otherwise they apply on next
    activation.
    """
    if all(v is None for v in (new_name, min_memory, memory, max_memory,
                               min_procs, procs, max_procs, min_vcpus, vcpus, max_vcpus,
                               dedicated, capped)):
        err_console.print("[yellow]Nothing to change — pass at least one option[/yellow]")
        raise typer.Exit(code=2)

    async def _go():
        async with _client() as hmc:
            uuid = await _resolve_partition_uuid(hmc, name_or_uuid)
            if uuid is None:
                return None, None
            if not yes:
                if not typer.confirm(f"Apply resource changes to '{name_or_uuid}' ({uuid})?"):
                    raise typer.Abort()
            xml = build_lpar_document(
                name=new_name,
                resources=LparResources(
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
                ),
            )
            return uuid, await hmc.modify_logical_partition(uuid, xml)

    uuid, updated = _run(_go)

    if uuid is None:
        err_console.print(f"[yellow]Partition '{name_or_uuid}' not found[/yellow]")
        raise typer.Exit(code=1)
    console.print(f"[green]Modified LPAR {uuid}[/green]")
    _print_json(updated)


@lpars_app.command("delete")
def lpars_delete(
    name_or_uuid: str = typer.Argument(..., help="Partition name or UUID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete (destroy) an LPAR. It must be powered off first."""

    async def _go():
        async with _client() as hmc:
            uuid = await _resolve_partition_uuid(hmc, name_or_uuid)
            if uuid is None:
                return None
            if not yes:
                if not typer.confirm(
                    f"Permanently DELETE partition '{name_or_uuid}' ({uuid})? This cannot be undone."
                ):
                    raise typer.Abort()
            await hmc.delete_logical_partition(uuid)
            return uuid

    uuid = _run(_go)

    if uuid is None:
        err_console.print(f"[yellow]Partition '{name_or_uuid}' not found[/yellow]")
        raise typer.Exit(code=1)
    console.print(f"[green]Deleted LPAR {uuid}[/green]")




@lpars_app.command("get-description")
def lpars_get_description(
    lpar_name: str = typer.Argument(..., help="LPAR name"),
    system_name: str = typer.Argument(..., help="Managed system name"),
) -> None:
    """Get the description field of an LPAR (HMC CLI via SSH)."""
    result = _run(
        lambda: get_lpar_description(_ssh_config(), system_name, lpar_name)
    )

    console.print(result.strip() or "(no description set)")


@lpars_app.command("set-description")
def lpars_set_description(
    lpar_name: str = typer.Argument(..., help="LPAR name"),
    system_name: str = typer.Argument(..., help="Managed system name"),
    description: str = typer.Argument(..., help="New description text"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Set the description field of an LPAR (HMC CLI via SSH)."""
    if not yes and not typer.confirm(
        f"Set description on '{lpar_name}' (system {system_name})?"
    ):
        raise typer.Abort()
    result = _run(
        lambda: set_lpar_description(_ssh_config(), system_name, lpar_name, description)
    )

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
    info = _run(
        lambda: get_lpar_proc_compat(_ssh_config(), system_name, lpar_name)
    )

    pend = info["pend"]
    curr = info["curr"]

    if as_json:
        _print_json(info)
    else:
        table = Table(title=f"Processor Compatibility Mode: {lpar_name}")
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="green")
        table.add_row("Pending Mode", pend or "-")
        table.add_row("Current Mode", curr or "-")
        console.print(table)


@lpars_app.command("set-proc-compat")
def lpars_set_proc_compat(
    lpar_name: str = typer.Argument(..., help="LPAR name"),
    system_name: str = typer.Argument(..., help="Managed system name"),
    mode: str = typer.Argument(..., help="Processor compatibility mode to set"),
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

    console.print(f"[green]Processor compatibility mode updated for '{lpar_name}'[/green]")
    if result.strip():
        console.print(result.strip())


