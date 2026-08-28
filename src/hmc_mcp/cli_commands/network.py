"""CLI commands for virtual networks, switches, bridges, SR-IOV mode, and vNICs."""

from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import asdict
from decimal import Decimal
import sys

import typer
from rich.table import Table

from .runtime import _run, _ssh_config, _with_client
from .output import _first_field, _output, _print_json, console

from ..operations.network import (
    create_virtual_network,
    delete_virtual_network,
    list_network_bridges,
    list_virtual_networks,
    list_virtual_switches,
)
from ..operations.pcie import (
    assign_dedicated_pcie_slot,
    list_dedicated_slots,
    list_sriov_adapters,
    list_sriov_logical_ports,
    list_sriov_physical_ports,
    unassign_dedicated_pcie_slot,
    assign_sriov_logical_port,
    set_sriov_adapter_mode,
    unassign_sriov_logical_port,
)
from ..operations.vnic import (
    VnicBackingSelector,
    VnicPartialError,
    add_vnic,
    list_fc_ports,
    list_sea_adapters,
    list_vnics,
    remove_vnic,
)
from ..ssh.network import PciClass, SriovMode, list_io_slots


def _confirm_on_stderr(prompt: str) -> bool:
    """Keep confirmation prompts and terminal input echoes off JSON stdout."""
    with redirect_stdout(sys.stderr):
        return typer.confirm(prompt, err=True)


def _print_pcie_inventory(result, as_json: bool) -> None:
    if as_json:
        _print_json(asdict(result))
        return
    if result.capability == "capability-unavailable":
        console.print(f"Capability unavailable: {result.unavailable_reason}")
        return
    if not result.items:
        console.print(f"{result.resource_kind} available; no items found")
        return

    rows = [asdict(item) for item in result.items]
    table = Table(title=f"{result.resource_kind} inventory on {result.system}")
    for field_name in rows[0]:
        table.add_column(field_name)
    for row in rows:
        table.add_row(
            *(str(value) if value is not None else "-" for value in row.values())
        )
    console.print(table)


def network_list_dedicated_pcie_slots(
    system_name: str = typer.Argument(..., help="Managed system name or UUID"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List normalized dedicated PCIe slots on a managed system."""
    result = _run(lambda: list_dedicated_slots(_ssh_config(), system_name))
    _print_pcie_inventory(result, as_json)


def network_assign_dedicated_pcie_slot(
    system_name: str,
    lpar_name: str,
    profile_name: str,
    drc_index: str,
    ownership_override: bool = typer.Option(False, "--ownership-override"),
) -> None:
    """Assign a dedicated slot when safe profile readback is available."""
    _with_client(
        lambda hmc: assign_dedicated_pcie_slot(
            hmc,
            system_name,
            lpar_name,
            profile_name,
            drc_index,
            ownership_override=ownership_override,
        )
    )


def network_unassign_dedicated_pcie_slot(
    system_name: str,
    lpar_name: str,
    profile_name: str,
    drc_index: str,
    ownership_override: bool = typer.Option(False, "--ownership-override"),
) -> None:
    """Unassign a dedicated slot when safe profile readback is available."""
    _with_client(
        lambda hmc: unassign_dedicated_pcie_slot(
            hmc,
            system_name,
            lpar_name,
            profile_name,
            drc_index,
            ownership_override=ownership_override,
        )
    )


def network_list_sriov_adapters(
    system_name: str = typer.Argument(..., help="Managed system name or UUID"),
    adapter_id: str | None = typer.Option(None, "--adapter-id"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List normalized SR-IOV adapters or their unavailable capability."""
    result = _run(lambda: list_sriov_adapters(_ssh_config(), system_name, adapter_id))
    _print_pcie_inventory(result, as_json)


def network_list_sriov_physical_ports(
    system_name: str = typer.Argument(..., help="Managed system name or UUID"),
    adapter_id: str | None = typer.Option(None, "--adapter-id"),
    physical_port_id: str | None = typer.Option(None, "--physical-port-id"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List normalized SR-IOV physical ports or their unavailable capability."""
    result = _run(
        lambda: list_sriov_physical_ports(
            _ssh_config(), system_name, adapter_id, physical_port_id
        )
    )
    _print_pcie_inventory(result, as_json)


def network_list_sriov_logical_ports(
    system_name: str = typer.Argument(..., help="Managed system name or UUID"),
    adapter_id: str | None = typer.Option(None, "--adapter-id"),
    physical_port_id: str | None = typer.Option(None, "--physical-port-id"),
    logical_port_id: str | None = typer.Option(None, "--logical-port-id"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List normalized SR-IOV logical ports or their unavailable capability."""
    result = _run(
        lambda: list_sriov_logical_ports(
            _ssh_config(),
            system_name,
            adapter_id,
            physical_port_id,
            logical_port_id,
        )
    )
    _print_pcie_inventory(result, as_json)


def network_assign_sriov_logical_port(
    system_name: str,
    lpar_name: str,
    adapter_id: str,
    physical_port_id: str,
    logical_port_id: str,
    capacity_percent: float,
    profile_name: str = typer.Option(..., "--profile-name"),
    ownership_override: bool = typer.Option(False, "--ownership-override"),
) -> None:
    """Assign an evidence-backed Ethernet SR-IOV logical port."""
    result = _with_client(
        lambda hmc: assign_sriov_logical_port(
            hmc,
            system_name,
            lpar_name,
            adapter_id,
            physical_port_id,
            logical_port_id,
            Decimal(str(capacity_percent)),
            profile_name=profile_name,
            ownership_override=ownership_override,
        )
    )
    _print_json(asdict(result))


def network_unassign_sriov_logical_port(
    system_name: str,
    lpar_name: str,
    adapter_id: str,
    physical_port_id: str,
    logical_port_id: str,
    profile_name: str = typer.Option(..., "--profile-name"),
    ownership_override: bool = typer.Option(False, "--ownership-override"),
) -> None:
    """Unassign a profile logical port on a Not Activated LPAR."""
    result = _with_client(
        lambda hmc: unassign_sriov_logical_port(
            hmc,
            system_name,
            lpar_name,
            adapter_id,
            physical_port_id,
            logical_port_id,
            profile_name=profile_name,
            ownership_override=ownership_override,
        )
    )
    _print_json(asdict(result))


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


def network_create(
    system: str = typer.Argument(..., help="Managed system UUID"),
    name: str = typer.Option(..., "--name", "-n", help="Network name"),
    vlan: int = typer.Option(..., "--vlan", help="VLAN ID"),
    virtual_switch_id: int = typer.Option(
        ..., "--virtual-switch-id", help="Backing VirtualSwitch numeric SwitchID"
    ),
    tagged: bool = typer.Option(False, "--tagged", help="Tagged (bridged) network"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Create a Virtual Network (VLAN) on a managed system."""
    if not yes and not typer.confirm(
        f"Create network '{name}' (VLAN {vlan}, switch ID {virtual_switch_id}) on {system}?"
    ):
        raise typer.Abort()

    net = _with_client(
        lambda hmc: create_virtual_network(
            hmc, system, name, vlan, virtual_switch_id, tagged=tagged
        )
    )

    console.print(f"[green]Created virtual network '{name}'[/green]")
    _print_json(net)


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


def network_list_bridges(
    system: str = typer.Argument(..., help="Managed system UUID"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List NetworkBridges (Shared Ethernet Adapters) on a managed system."""

    bridges = _with_client(lambda hmc: list_network_bridges(hmc, system))

    _output(bridges, as_json, None, "No network bridges found")


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


def network_set_sriov_mode(
    system_name: str = typer.Argument(..., help="Managed system name or UUID"),
    adapter_id: str = typer.Argument(
        ..., help="Physical adapter ID (from `hmc-mcp network list-io-slots`)"
    ),
    mode: SriovMode = typer.Argument(..., help="'sriov' or 'dedicated'"),
) -> None:
    """Verify an adapter's current mode; transitions fail closed."""
    result = _run(
        lambda: set_sriov_adapter_mode(_ssh_config(), system_name, adapter_id, mode)
    )

    console.print(
        f"[green]Adapter {adapter_id} verified in '{mode}' mode on '{system_name}'[/green]"
    )
    if result.strip():
        console.print(result.strip())


def network_list_vnics(
    system_name: str = typer.Argument(..., help="Managed system name or UUID"),
    lpar: str = typer.Argument(..., help="LPAR name or UUID"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List vNICs (SR-IOV-backed Virtual NICs) on an LPAR (HMC CLI via SSH)."""
    vnics = _run(lambda: list_vnics(_ssh_config(), system_name, lpar))
    _output(vnics, as_json, None, "No vNICs found")


def network_add_vnic(
    system_name: str = typer.Argument(..., help="Managed system name or UUID"),
    lpar: str = typer.Argument(..., help="LPAR name or UUID"),
    vios_name: str = typer.Option(..., "--vios-name"),
    vios_lpar_id: str = typer.Option(..., "--vios-lpar-id"),
    adapter_id: str = typer.Option(..., "--adapter-id"),
    physical_port_id: str = typer.Option(..., "--physical-port-id"),
    capacity_percent: float = typer.Option(..., "--capacity-percent"),
    port_vlan_id: int = typer.Option(..., "--port-vlan-id"),
    ownership_override: bool = typer.Option(False, "--ownership-override"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Add and verify a vNIC with one typed SR-IOV backing selector."""
    if not yes and not _confirm_on_stderr(
        f"Add vNIC (VIOS={vios_name}, adapter={adapter_id}, "
        f"port={physical_port_id}, capacity={capacity_percent}, vlan={port_vlan_id}) "
        f"to '{lpar}' on '{system_name}'?"
    ):
        raise typer.Abort()

    async def operation(hmc):
        try:
            return await add_vnic(
                hmc,
                system_name,
                lpar,
                VnicBackingSelector(
                    vios_name,
                    vios_lpar_id,
                    adapter_id,
                    physical_port_id,
                    Decimal(str(capacity_percent)),
                ),
                port_vlan_id,
                ownership_override=ownership_override,
            )
        except VnicPartialError as exc:
            return exc

    outcome = _with_client(operation)
    result = outcome.result if isinstance(outcome, VnicPartialError) else outcome
    _print_json(asdict(result))
    if isinstance(outcome, VnicPartialError):
        raise typer.Exit(1)


def network_remove_vnic(
    system_name: str = typer.Argument(..., help="Managed system name or UUID"),
    lpar: str = typer.Argument(..., help="LPAR name or UUID"),
    slot_num: str = typer.Argument(..., help="vNIC slot number (from list-vnics)"),
    ownership_override: bool = typer.Option(False, "--ownership-override"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Remove a vNIC from an LPAR (HMC CLI via SSH)."""
    if not yes and not _confirm_on_stderr(
        f"Remove vNIC slot {slot_num} from '{lpar}' on '{system_name}'?"
    ):
        raise typer.Abort()

    async def operation(hmc):
        try:
            return await remove_vnic(
                hmc,
                system_name,
                lpar,
                slot_num,
                ownership_override=ownership_override,
            )
        except VnicPartialError as exc:
            return exc

    outcome = _with_client(operation)
    result = outcome.result if isinstance(outcome, VnicPartialError) else outcome
    _print_json(asdict(result))
    if isinstance(outcome, VnicPartialError):
        raise typer.Exit(1)


def register_commands(group: typer.Typer) -> None:
    """Register this module’s commands on *group*."""
    group.command("list-dedicated-pcie-slots")(network_list_dedicated_pcie_slots)
    group.command("assign-dedicated-pcie-slot")(network_assign_dedicated_pcie_slot)
    group.command("unassign-dedicated-pcie-slot")(network_unassign_dedicated_pcie_slot)
    group.command("list-sriov-adapters")(network_list_sriov_adapters)
    group.command("list-sriov-physical-ports")(network_list_sriov_physical_ports)
    group.command("list-sriov-logical-ports")(network_list_sriov_logical_ports)
    group.command("assign-sriov-logical-port")(network_assign_sriov_logical_port)
    group.command("unassign-sriov-logical-port")(network_unassign_sriov_logical_port)
    group.command("list-switches")(network_list_switches)
    group.command("list-networks")(network_list_networks)
    group.command("create")(network_create)
    group.command("delete")(network_delete)
    group.command("list-bridges")(network_list_bridges)
    group.command("list-fc-ports")(network_list_fc_ports)
    group.command("list-sea-adapters")(network_list_sea_adapters)
    group.command("list-io-slots")(network_list_io_slots)
    group.command("set-sriov-mode")(network_set_sriov_mode)
    group.command("list-vnics")(network_list_vnics)
    group.command("add-vnic")(network_add_vnic)
    group.command("remove-vnic")(network_remove_vnic)
