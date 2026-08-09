"""CLI commands for virtual networks, switches, bridges, SR-IOV mode, and vNICs.
"""

from __future__ import annotations

import shlex

import typer
from rich.table import Table

from .cli_app import (
    _first_field,
    _output,
    _print_json,
    _run,
    _ssh_config,
    _with_client,
    console,
    err_console,
    network_app,
    run_hmc,
)

from .ssh import (
    list_fc_ports,
    list_sea_adapters,
    list_vnics,
)



@network_app.command("list-switches")
def network_list_switches(
    system: str = typer.Argument(..., help="Managed system UUID"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List VirtualSwitches on a managed system."""

    switches = _with_client(lambda hmc: hmc.list_virtual_switches(system))

    table = None
    if not as_json:
        table = Table(title=f"Virtual Switches on {system}")
        for col in ("Name", "SwitchID", "Mode", "UUID"):
            table.add_column(col)
        for s in switches:
            table.add_row(
                _first_field(s, "SwitchName"),
                _first_field(s, "SwitchID"),
                _first_field(s, "SwitchMode"),
                s.get("UUID") or "-",
            )
    _output(switches, as_json, table, "No virtual switches found")


@network_app.command("list-networks")
def network_list_networks(
    system: str = typer.Argument(..., help="Managed system UUID"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List Virtual Networks (VLANs) on a managed system."""

    nets = _with_client(lambda hmc: hmc.list_virtual_networks(system))

    table = None
    if not as_json:
        table = Table(title=f"Virtual Networks on {system}")
        for col in ("Name", "VLAN", "VswitchID", "Tagged", "UUID"):
            table.add_column(col)
        for n in nets:
            table.add_row(
                _first_field(n, "NetworkName"),
                _first_field(n, "NetworkVLANID"),
                _first_field(n, "VswitchID"),
                _first_field(n, "TaggedNetwork"),
                n.get("UUID") or "-",
            )
    _output(nets, as_json, table, "No virtual networks found")


@network_app.command("create")
def network_create(
    system: str = typer.Argument(..., help="Managed system UUID"),
    name: str = typer.Option(..., "--name", "-n", help="Network name"),
    vlan: int = typer.Option(..., "--vlan", help="VLAN ID"),
    vswitch: int = typer.Option(..., "--vswitch", help="Backing VirtualSwitch SwitchID"),
    tagged: bool = typer.Option(False, "--tagged", help="Tagged (bridged) network"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Create a Virtual Network (VLAN) on a managed system."""
    if not yes and not typer.confirm(f"Create network '{name}' (VLAN {vlan}, vswitch {vswitch}) on {system}?"):
        raise typer.Abort()

    net = _with_client(
        lambda hmc: hmc.create_virtual_network(system, name, vlan, vswitch, tagged=tagged)
    )

    console.print(f"[green]Created virtual network '{name}'[/green]")
    _print_json(net)


@network_app.command("delete")
def network_delete(
    system: str = typer.Argument(..., help="Managed system UUID"),
    uuid: str = typer.Option(..., "--uuid", help="Virtual Network UUID to delete"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Delete a Virtual Network from a managed system."""
    if not yes and not typer.confirm(f"Delete virtual network {uuid} from {system}?"):
        raise typer.Abort()

    _with_client(lambda hmc: hmc.delete_virtual_network(system, uuid))

    console.print(f"[green]Deleted virtual network {uuid}[/green]")


@network_app.command("list-bridges")
def network_list_bridges(
    system: str = typer.Argument(..., help="Managed system UUID"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List NetworkBridges (Shared Ethernet Adapters) on a managed system."""

    bridges = _with_client(lambda hmc: hmc.list_network_bridges(system))

    _output(bridges, as_json, None, "No network bridges found")


@network_app.command("list-fc-ports")
def network_list_fc_ports(
    system: str = typer.Argument(..., help="Managed system name"),
    lpar_name: str | None = typer.Option(None, "--lpar", help="Filter by LPAR name"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List Virtual Fibre Channel (NPIV) adapters on a managed system."""

    ports = _run(lambda: list_fc_ports(_ssh_config(), system, lpar_name))

    _output(ports, as_json, None, "No FC ports found")


@network_app.command("list-sea-adapters")
def network_list_sea_adapters(
    system: str = typer.Argument(..., help="Managed system name"),
    lpar_name: str | None = typer.Option(None, "--lpar", help="Filter by LPAR name"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List Shared Ethernet Adapter (SEA) virtual Ethernet ports on a managed system."""

    adapters = _run(lambda: list_sea_adapters(_ssh_config(), system, lpar_name))

    _output(adapters, as_json, None, "No SEA adapters found")


@network_app.command("set-sriov-mode")
def network_set_sriov_mode(
    system_name: str = typer.Argument(..., help="Managed system name"),
    adapter_id: str = typer.Argument(..., help="Physical adapter ID (from `hmc-mcp network list-io-slots`)"),
    mode: str = typer.Argument(..., help="'sriov' or 'dedicated'"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Toggle a physical SR-IOV adapter between SR-IOV and dedicated mode (HMC CLI via SSH)."""
    if mode not in {"sriov", "dedicated"}:
        err_console.print(f"[red]Invalid mode {mode!r}. Must be 'sriov' or 'dedicated'.[/red]")
        raise typer.Exit(code=2)
    if not yes and not typer.confirm(
        f"Set adapter {adapter_id} on system '{system_name}' to '{mode}' mode?"
    ):
        raise typer.Abort()
    payload = f"sriov_adapter_mode={mode}"
    result = run_hmc(
        f"chhwres -r sriov -m {shlex.quote(system_name)} -o s --id {shlex.quote(adapter_id)} "
        f"-a {shlex.quote(payload)}",
    )

    console.print(f"[green]Adapter {adapter_id} set to '{mode}' mode on '{system_name}'[/green]")
    if result.strip():
        console.print(result.strip())


@network_app.command("list-vnics")
def network_list_vnics(
    system: str = typer.Argument(..., help="Managed system name"),
    lpar: str = typer.Argument(..., help="LPAR name"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List vNICs (SR-IOV-backed Virtual NICs) on an LPAR (HMC CLI via SSH)."""
    config = _ssh_config()
    vnics = _run(lambda: list_vnics(config, system, lpar))
    _output(vnics, as_json, None, "No vNICs found")


@network_app.command("add-vnic")
def network_add_vnic(
    system: str = typer.Argument(..., help="Managed system name"),
    lpar: str = typer.Argument(..., help="LPAR name"),
    capacity: int = typer.Option(..., "--capacity", "-c", help="vNIC capacity (1–100)"),
    vswitch: str = typer.Option(..., "--vswitch", help="Virtual switch name"),
    vlan: int = typer.Option(..., "--vlan", help="Port VLAN ID"),
    backing_devices: str | None = typer.Option(None, "--backing-devices", help="Backing devices (opaque string, v1 only)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Add a vNIC to an LPAR (HMC CLI via SSH, v1 minimal parameters)."""
    if not yes and not typer.confirm(
        f"Add vNIC (capacity={capacity}, vswitch={vswitch}, vlan={vlan}) to '{lpar}' on '{system}'?"
    ):
        raise typer.Abort()

    attrs = f"capacity={capacity},vswitch_name={vswitch},port_vlan_id={vlan}"
    if backing_devices:
        attrs += f",backing_devices={backing_devices}"

    result = run_hmc(
        f"chhwres -r virtualio --rsubtype vnic -o a -m {shlex.quote(system)} "
        f"--filter lpar_names={shlex.quote(lpar)} "
        f"-a {shlex.quote(attrs)}",
    )

    console.print(f"[green]vNIC added to '{lpar}' on '{system}'[/green]")
    if result.strip():
        console.print(result.strip())


@network_app.command("remove-vnic")
def network_remove_vnic(
    system: str = typer.Argument(..., help="Managed system name"),
    lpar: str = typer.Argument(..., help="LPAR name"),
    vnic_id: str = typer.Argument(..., help="vNIC ID (from list-vnics)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Remove a vNIC from an LPAR (HMC CLI via SSH)."""
    if not yes and not typer.confirm(
        f"Remove vNIC {vnic_id} from '{lpar}' on '{system}'?"
    ):
        raise typer.Abort()

    payload = f"vnic_id={vnic_id}"
    result = run_hmc(
        f"chhwres -r virtualio --rsubtype vnic -o r -m {shlex.quote(system)} "
        f"--filter lpar_names={shlex.quote(lpar)} "
        f"-a {shlex.quote(payload)}",
    )

    console.print(f"[green]vNIC {vnic_id} removed from '{lpar}' on '{system}'[/green]")
    if result.strip():
        console.print(result.strip())


