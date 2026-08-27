"""CLI commands for virtual adapters (network / storage) on LPARs."""

from __future__ import annotations


import typer

from ..client.client_adapters import ADAPTER_TYPES, AdapterType

from .app import (
    _client,
    _output,
    _print_json,
    _run,
    adapters_app,
    console,
)
from ..operations.adapters import (
    add_network_adapter,
    add_vios_adapter,
    delete_adapter,
    list_adapters,
)


_ADAPTER_TYPES = " | ".join(sorted(ADAPTER_TYPES))


@adapters_app.command("list")
def adapters_list(
    lpar: str = typer.Argument(..., help="LPAR name or UUID"),
    adapter_type: AdapterType = typer.Option(
        "ClientNetworkAdapter", "--type", "-t", help=_ADAPTER_TYPES
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List an LPAR's virtual adapters of a given type."""

    async def _go():
        async with _client() as hmc:
            return await list_adapters(hmc, lpar, adapter_type)

    adapters = _run(_go)

    _output(adapters, as_json, None, f"No {adapter_type} adapters on {lpar}")


@adapters_app.command("add-network")
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
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Add a Virtual Ethernet (network) adapter to an LPAR."""

    if not yes and not typer.confirm(f"Add network adapter (VLAN {vlan}) to '{lpar}'?"):
        raise typer.Abort()

    async def _go():
        async with _client() as hmc:
            return await add_network_adapter(
                hmc,
                lpar,
                vlan,
                slot_number=slot,
                virtual_switch_id=virtual_switch_id,
                tagged=tagged,
                mac_address=mac,
            )

    _adapter_mutation(_go, lpar, "network")


@adapters_app.command("add-vscsi")
def adapters_add_vscsi(
    lpar: str = typer.Argument(..., help="LPAR name or UUID"),
    vios_id: int = typer.Option(..., "--vios-id", help="VIOS PartitionID (integer)"),
    vios_slot: int = typer.Option(..., "--vios-slot", help="VIOS server-side slot"),
    slot: int | None = typer.Option(
        None, "--slot", help="Client virtual slot (auto if omitted)"
    ),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Add a Virtual SCSI client adapter, paired to a VIOS."""

    if not yes and not typer.confirm(
        f"Add vSCSI adapter to '{lpar}' via VIOS {vios_id}?"
    ):
        raise typer.Abort()

    async def _go():
        async with _client() as hmc:
            return await add_vios_adapter(
                hmc, lpar, vios_id, vios_slot, slot, fibre_channel=False
            )

    _adapter_mutation(_go, lpar, "vSCSI")


@adapters_app.command("add-vfc")
def adapters_add_vfc(
    lpar: str = typer.Argument(..., help="LPAR name or UUID"),
    vios_id: int = typer.Option(..., "--vios-id", help="VIOS PartitionID (integer)"),
    vios_slot: int = typer.Option(..., "--vios-slot", help="VIOS server-side FC slot"),
    slot: int | None = typer.Option(
        None, "--slot", help="Client virtual slot (auto if omitted)"
    ),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Add a Virtual Fibre Channel (NPIV) client adapter, paired to a VIOS."""

    if not yes and not typer.confirm(
        f"Add vFC adapter to '{lpar}' via VIOS {vios_id}?"
    ):
        raise typer.Abort()

    async def _go():
        async with _client() as hmc:
            return await add_vios_adapter(
                hmc, lpar, vios_id, vios_slot, slot, fibre_channel=True
            )

    _adapter_mutation(_go, lpar, "vFC")


@adapters_app.command("delete")
def adapters_delete(
    lpar: str = typer.Argument(..., help="LPAR name or UUID"),
    adapter_type: AdapterType = typer.Option(..., "--type", "-t", help=_ADAPTER_TYPES),
    adapter_uuid: str = typer.Option(
        ..., "--uuid", help="Adapter UUID (from `adapters list`)"
    ),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Remove a virtual adapter from an LPAR."""

    if not yes and not typer.confirm(
        f"Delete {adapter_type} {adapter_uuid} from '{lpar}'?"
    ):
        raise typer.Abort()

    async def _go():
        async with _client() as hmc:
            return await delete_adapter(hmc, lpar, adapter_type, adapter_uuid)

    uuid = _run(_go)

    console.print(f"[green]Deleted {adapter_type} {adapter_uuid}[/green] from {uuid}")


def _adapter_mutation(go_coro, lpar: str, kind: str) -> None:
    result = _run(go_coro)
    console.print(f"[green]Added {kind} adapter[/green] to {result.lpar_uuid}")
    _print_json(result.resource)
