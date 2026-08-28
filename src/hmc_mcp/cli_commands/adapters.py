"""CLI commands for virtual adapters (network / storage) on LPARs."""

from __future__ import annotations


import typer

from ..client.client_adapters import ADAPTER_TYPES, AdapterType

from .runtime import client, run
from .output import output, print_json, console
from ..operations.adapters import (
    add_network_adapter,
    add_vfc_adapter,
    add_vscsi_adapter,
    delete_adapter,
    list_adapters,
)


_ADAPTER_TYPES = " | ".join(sorted(ADAPTER_TYPES))


def adapters_list(
    lpar: str = typer.Argument(..., help="LPAR name or UUID"),
    adapter_type: AdapterType = typer.Option(
        "ClientNetworkAdapter", "--type", "-t", help=_ADAPTER_TYPES
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List an LPAR's virtual adapters of a given type."""

    async def _go():
        async with client() as hmc:
            return await list_adapters(hmc, None, lpar, adapter_type)

    adapters = run(_go)

    output(adapters, as_json, None, f"No {adapter_type} adapters on {lpar}")


def adapters_add_network(
    lpar: str = typer.Argument(..., help="LPAR name or UUID"),
    vlan: int = typer.Option(..., "--vlan", help="Port VLAN ID (PVID)"),
    slot: int | None = typer.Option(
        None, "--slot", help="Virtual slot (auto if omitted)"
    ),
    virtual_switch_id: int | None = typer.Option(
        None, "--virtual-switch-id", help="VirtualSwitch numeric SwitchID"
    ),
    tagged: bool = typer.Option(
        False, "--tagged", help="VLAN-tagged (trunking) adapter"
    ),
    mac: str | None = typer.Option(None, "--mac", help="Pin the MAC address"),
    ownership_override: bool = typer.Option(False, "--ownership-override"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Add a Virtual Ethernet (network) adapter to an LPAR."""

    if not yes and not typer.confirm(f"Add network adapter (VLAN {vlan}) to '{lpar}'?"):
        raise typer.Abort()

    async def _go():
        async with client() as hmc:
            return await add_network_adapter(
                hmc,
                None,
                lpar,
                vlan,
                slot_number=slot,
                virtual_switch_id=virtual_switch_id,
                tagged=tagged,
                mac_address=mac,
                ownership_override=ownership_override,
            )

    _adapter_mutation(_go, lpar, "network")


def adapters_add_vscsi(
    lpar: str = typer.Argument(..., help="LPAR name or UUID"),
    vios_id: int = typer.Option(..., "--vios-id", help="VIOS PartitionID (integer)"),
    vios_slot: int = typer.Option(..., "--vios-slot", help="VIOS server-side slot"),
    slot: int | None = typer.Option(
        None, "--slot", help="Client virtual slot (auto if omitted)"
    ),
    yes: bool = typer.Option(False, "--yes", "-y"),
    ownership_override: bool = typer.Option(False, "--ownership-override"),
) -> None:
    """Add a Virtual SCSI client adapter, paired to a VIOS."""

    if not yes and not typer.confirm(
        f"Add vSCSI adapter to '{lpar}' via VIOS {vios_id}?"
    ):
        raise typer.Abort()

    async def _go():
        async with client() as hmc:
            return await add_vscsi_adapter(
                hmc,
                None,
                lpar,
                vios_id,
                vios_slot,
                slot_number=slot,
                ownership_override=ownership_override,
            )

    _adapter_mutation(_go, lpar, "vSCSI")


def adapters_add_vfc(
    lpar: str = typer.Argument(..., help="LPAR name or UUID"),
    vios_id: int = typer.Option(..., "--vios-id", help="VIOS PartitionID (integer)"),
    vios_slot: int = typer.Option(..., "--vios-slot", help="VIOS server-side FC slot"),
    slot: int | None = typer.Option(
        None, "--slot", help="Client virtual slot (auto if omitted)"
    ),
    yes: bool = typer.Option(False, "--yes", "-y"),
    ownership_override: bool = typer.Option(False, "--ownership-override"),
) -> None:
    """Add a Virtual Fibre Channel (NPIV) client adapter, paired to a VIOS."""

    if not yes and not typer.confirm(
        f"Add vFC adapter to '{lpar}' via VIOS {vios_id}?"
    ):
        raise typer.Abort()

    async def _go():
        async with client() as hmc:
            return await add_vfc_adapter(
                hmc,
                None,
                lpar,
                vios_id,
                vios_slot,
                slot_number=slot,
                ownership_override=ownership_override,
            )

    _adapter_mutation(_go, lpar, "vFC")


def adapters_delete(
    lpar: str = typer.Argument(..., help="LPAR name or UUID"),
    adapter_type: AdapterType = typer.Option(..., "--type", "-t", help=_ADAPTER_TYPES),
    adapter_uuid: str = typer.Option(
        ..., "--uuid", help="Adapter UUID (from `adapters list`)"
    ),
    yes: bool = typer.Option(False, "--yes", "-y"),
    ownership_override: bool = typer.Option(False, "--ownership-override"),
) -> None:
    """Remove a virtual adapter from an LPAR."""

    if not yes and not typer.confirm(
        f"Delete {adapter_type} {adapter_uuid} from '{lpar}'?"
    ):
        raise typer.Abort()

    async def _go():
        async with client() as hmc:
            return await delete_adapter(
                hmc,
                None,
                lpar,
                adapter_type,
                adapter_uuid,
                ownership_override=ownership_override,
            )

    deleted_uuid = run(_go)

    console.print(f"[green]Deleted {adapter_type} {deleted_uuid}[/green] from {lpar}")


def _adapter_mutation(go_coro, lpar: str, kind: str) -> None:
    result = run(go_coro)
    console.print(f"[green]Added {kind} adapter[/green] to {result.lpar_uuid}")
    print_json(result.resource)


def register_commands(group: typer.Typer) -> None:
    """Register this module’s commands on *group*."""
    group.command("list")(adapters_list)
    group.command("add-network")(adapters_add_network)
    group.command("add-vscsi")(adapters_add_vscsi)
    group.command("add-vfc")(adapters_add_vfc)
    group.command("delete")(adapters_delete)
