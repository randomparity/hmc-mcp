"""CLI commands for virtual networks, switches, bridges, SR-IOV mode, and vNICs."""

from __future__ import annotations

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
    network_app,
)

from .operations_network import (
    create_virtual_network,
    delete_virtual_network,
    list_network_bridges,
    list_virtual_networks,
    list_virtual_switches,
)
from .operations_ssh_network import (
    SriovMode,
    add_vnic,
    list_fc_ports,
    list_sea_adapters,
    list_vnics,
    remove_vnic,
    set_sriov_adapter_mode,
)
from .ssh_commands import PciClass, list_io_slots


@network_app.command("list-switches")
def network_list_switches(
    system: str = typer.Argument(..., help="Managed system UUID"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List VirtualSwitches on a managed system."""

    switches = _with_client(lambda hmc: list_virtual_switches(hmc, system))

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

    nets = _with_client(lambda hmc: list_virtual_networks(hmc, system))

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
    vswitch: int = typer.Option(
        ..., "--vswitch", help="Backing VirtualSwitch SwitchID"
    ),
    tagged: bool = typer.Option(False, "--tagged", help="Tagged (bridged) network"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Create a Virtual Network (VLAN) on a managed system."""
    if not yes and not typer.confirm(
        f"Create network '{name}' (VLAN {vlan}, vswitch {vswitch}) on {system}?"
    ):
        raise typer.Abort()

    net = _with_client(
        lambda hmc: create_virtual_network(
            hmc, system, name, vlan, vswitch, tagged=tagged
        )
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

    _with_client(lambda hmc: delete_virtual_network(hmc, system, uuid))

    console.print(f"[green]Deleted virtual network {uuid}[/green]")


@network_app.command("list-bridges")
def network_list_bridges(
    system: str = typer.Argument(..., help="Managed system UUID"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List NetworkBridges (Shared Ethernet Adapters) on a managed system."""

    bridges = _with_client(lambda hmc: list_network_bridges(hmc, system))

    _output(bridges, as_json, None, "No network bridges found")


@network_app.command("list-fc-ports")
def network_list_fc_ports(
    system_name: str = typer.Argument(..., help="Managed system name or UUID"),
    lpar_name: str | None = typer.Option(
        None, "--lpar", help="Filter by LPAR name or UUID"
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List Virtual Fibre Channel (NPIV) adapters on a managed system."""

    ports = _run(lambda: list_fc_ports(_ssh_config(), system_name, lpar_name))

    _output(ports, as_json, None, "No FC ports found")


@network_app.command("list-sea-adapters")
def network_list_sea_adapters(
    system_name: str = typer.Argument(..., help="Managed system name or UUID"),
    lpar_name: str | None = typer.Option(
        None, "--lpar", help="Filter by LPAR name or UUID"
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List Shared Ethernet Adapter (SEA) virtual Ethernet ports on a managed system."""

    adapters = _run(lambda: list_sea_adapters(_ssh_config(), system_name, lpar_name))

    _output(adapters, as_json, None, "No SEA adapters found")


@network_app.command("list-io-slots")
def network_list_io_slots(
    system_name: str = typer.Argument(..., help="Managed system name"),
    pci_class: PciClass = typer.Option(
        "all", "--pci-class", help="Filter by PCI class: all, eth, sas, san, nvme"
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List physical I/O slots on a managed system (HMC CLI via SSH)."""

    slots = _run(lambda: list_io_slots(_ssh_config(), system_name, pci_class))

    _output(slots, as_json, None, "No I/O slots found")


@network_app.command("set-sriov-mode")
def network_set_sriov_mode(
    system_name: str = typer.Argument(..., help="Managed system name or UUID"),
    adapter_id: str = typer.Argument(
        ..., help="Physical adapter ID (from `hmc-mcp network list-io-slots`)"
    ),
    mode: SriovMode = typer.Argument(..., help="'sriov' or 'dedicated'"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Toggle a physical SR-IOV adapter between SR-IOV and dedicated mode (HMC CLI via SSH)."""
    if not yes and not typer.confirm(
        f"Set adapter {adapter_id} on system '{system_name}' to '{mode}' mode?"
    ):
        raise typer.Abort()
    result = _run(
        lambda: set_sriov_adapter_mode(_ssh_config(), system_name, adapter_id, mode)
    )

    console.print(
        f"[green]Adapter {adapter_id} set to '{mode}' mode on '{system_name}'[/green]"
    )
    if result.strip():
        console.print(result.strip())


@network_app.command("list-vnics")
def network_list_vnics(
    system_name: str = typer.Argument(..., help="Managed system name or UUID"),
    lpar: str = typer.Argument(..., help="LPAR name or UUID"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List vNICs (SR-IOV-backed Virtual NICs) on an LPAR (HMC CLI via SSH)."""
    config = _ssh_config()
    vnics = _run(lambda: list_vnics(config, system_name, lpar))
    _output(vnics, as_json, None, "No vNICs found")


@network_app.command("add-vnic")
def network_add_vnic(
    system_name: str = typer.Argument(..., help="Managed system name or UUID"),
    lpar: str = typer.Argument(..., help="LPAR name or UUID"),
    capacity: int = typer.Option(..., "--capacity", "-c", help="vNIC capacity (1–100)"),
    vswitch: str = typer.Option(..., "--vswitch", help="Virtual switch name"),
    vlan: int = typer.Option(..., "--vlan", help="Port VLAN ID"),
    backing_devices: str | None = typer.Option(
        None, "--backing-devices", help="Backing devices (opaque string, v1 only)"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Add a vNIC to an LPAR (HMC CLI via SSH, v1 minimal parameters)."""
    if not yes and not typer.confirm(
        f"Add vNIC (capacity={capacity}, vswitch={vswitch}, vlan={vlan}) to '{lpar}' on '{system_name}'?"
    ):
        raise typer.Abort()

    result = _run(
        lambda: add_vnic(
            _ssh_config(), system_name, lpar, capacity, vswitch, vlan, backing_devices
        )
    )

    console.print(f"[green]vNIC added to '{lpar}' on '{system_name}'[/green]")
    if result.strip():
        console.print(result.strip())


@network_app.command("remove-vnic")
def network_remove_vnic(
    system_name: str = typer.Argument(..., help="Managed system name or UUID"),
    lpar: str = typer.Argument(..., help="LPAR name or UUID"),
    vnic_id: str = typer.Argument(..., help="vNIC ID (from list-vnics)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Remove a vNIC from an LPAR (HMC CLI via SSH)."""
    if not yes and not typer.confirm(
        f"Remove vNIC {vnic_id} from '{lpar}' on '{system_name}'?"
    ):
        raise typer.Abort()

    result = _run(lambda: remove_vnic(_ssh_config(), system_name, lpar, vnic_id))

    console.print(
        f"[green]vNIC {vnic_id} removed from '{lpar}' on '{system_name}'[/green]"
    )
    if result.strip():
        console.print(result.strip())
