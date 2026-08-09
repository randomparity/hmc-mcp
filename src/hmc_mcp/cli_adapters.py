"""CLI commands for virtual adapters (network / storage) on LPARs.
"""

from __future__ import annotations


import typer

from .cli_app import (
    _client,
    _output,
    _print_json,
    _resolve_uuid,
    _run,
    adapters_app,
    console,
    err_console,
)


_ADAPTER_TYPES = "ClientNetworkAdapter | VirtualSCSIClientAdapter | VirtualFibreChannelClientAdapter | VirtualNICDedicated"


@adapters_app.command("list")
def adapters_list(
    lpar: str = typer.Argument(..., help="LPAR name or UUID"),
    adapter_type: str = typer.Option("ClientNetworkAdapter", "--type", "-t", help=_ADAPTER_TYPES),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List an LPAR's virtual adapters of a given type."""

    async def _go():
        async with _client() as hmc:
            uuid = await _resolve_uuid(hmc, lpar)
            if uuid is None:
                return None, None
            return uuid, await hmc.list_child("LogicalPartition", uuid, adapter_type)

    uuid, adapters = _run(_go)

    if uuid is None:
        err_console.print(f"[yellow]Partition '{lpar}' not found[/yellow]")
        raise typer.Exit(code=1)
    _output(adapters, as_json, None, f"No {adapter_type} adapters on {lpar}")


@adapters_app.command("add-network")
def adapters_add_network(
    lpar: str = typer.Argument(..., help="LPAR name or UUID"),
    vlan: int = typer.Option(..., "--vlan", help="Port VLAN ID (PVID)"),
    slot: int | None = typer.Option(None, "--slot", help="Virtual slot (auto if omitted)"),
    vswitch: int | None = typer.Option(None, "--vswitch", help="VirtualSwitch ID"),
    tagged: bool = typer.Option(False, "--tagged", help="VLAN-tagged (trunking) adapter"),
    mac: str | None = typer.Option(None, "--mac", help="Pin the MAC address"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Add a Virtual Ethernet (network) adapter to an LPAR."""

    async def _go():
        async with _client() as hmc:
            uuid = await _resolve_uuid(hmc, lpar)
            if uuid is None:
                return None, None
            if not yes and not typer.confirm(f"Add network adapter (VLAN {vlan}) to '{lpar}' ({uuid})?"):
                raise typer.Abort()
            return uuid, await hmc.add_network_adapter(uuid, vlan, slot, vswitch, tagged, mac)

    _adapter_mutation(_go, lpar, "network")


@adapters_app.command("add-vscsi")
def adapters_add_vscsi(
    lpar: str = typer.Argument(..., help="LPAR name or UUID"),
    vios_id: int = typer.Option(..., "--vios-id", help="VIOS PartitionID (integer)"),
    vios_slot: int = typer.Option(..., "--vios-slot", help="VIOS server-side slot"),
    slot: int | None = typer.Option(None, "--slot", help="Client virtual slot (auto if omitted)"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Add a Virtual SCSI client adapter, paired to a VIOS."""

    async def _go():
        async with _client() as hmc:
            uuid = await _resolve_uuid(hmc, lpar)
            if uuid is None:
                return None, None
            if not yes and not typer.confirm(f"Add vSCSI adapter to '{lpar}' ({uuid}) via VIOS {vios_id}?"):
                raise typer.Abort()
            return uuid, await hmc.add_vscsi_adapter(uuid, vios_id, vios_slot, slot)

    _adapter_mutation(_go, lpar, "vSCSI")


@adapters_app.command("add-vfc")
def adapters_add_vfc(
    lpar: str = typer.Argument(..., help="LPAR name or UUID"),
    vios_id: int = typer.Option(..., "--vios-id", help="VIOS PartitionID (integer)"),
    vios_slot: int = typer.Option(..., "--vios-slot", help="VIOS server-side FC slot"),
    slot: int | None = typer.Option(None, "--slot", help="Client virtual slot (auto if omitted)"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Add a Virtual Fibre Channel (NPIV) client adapter, paired to a VIOS."""

    async def _go():
        async with _client() as hmc:
            uuid = await _resolve_uuid(hmc, lpar)
            if uuid is None:
                return None, None
            if not yes and not typer.confirm(f"Add vFC adapter to '{lpar}' ({uuid}) via VIOS {vios_id}?"):
                raise typer.Abort()
            return uuid, await hmc.add_vfc_adapter(uuid, vios_id, vios_slot, slot)

    _adapter_mutation(_go, lpar, "vFC")


@adapters_app.command("delete")
def adapters_delete(
    lpar: str = typer.Argument(..., help="LPAR name or UUID"),
    adapter_type: str = typer.Option(..., "--type", "-t", help=_ADAPTER_TYPES),
    adapter_uuid: str = typer.Option(..., "--uuid", help="Adapter UUID (from `adapters list`)"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Remove a virtual adapter from an LPAR."""

    async def _go():
        async with _client() as hmc:
            uuid = await _resolve_uuid(hmc, lpar)
            if uuid is None:
                return None
            if not yes and not typer.confirm(f"Delete {adapter_type} {adapter_uuid} from '{lpar}'?"):
                raise typer.Abort()
            await hmc.delete_child("LogicalPartition", uuid, adapter_type, adapter_uuid)
            return uuid

    uuid = _run(_go)

    if uuid is None:
        err_console.print(f"[yellow]Partition '{lpar}' not found[/yellow]")
        raise typer.Exit(code=1)
    console.print(f"[green]Deleted {adapter_type} {adapter_uuid}[/green] from {uuid}")



def _adapter_mutation(go_coro, lpar: str, kind: str) -> None:
    uuid, result = _run(go_coro)
    if uuid is None:
        err_console.print(f"[yellow]Partition '{lpar}' not found[/yellow]")
        raise typer.Exit(code=1)
    console.print(f"[green]Added {kind} adapter[/green] to {uuid}")
    _print_json(result)


