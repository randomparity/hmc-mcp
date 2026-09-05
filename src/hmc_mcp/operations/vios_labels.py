"""Internal shared VIOS FC label operations for MCP and CLI adapters."""

from __future__ import annotations

from collections.abc import Sequence

from ..config import HMCConfig
from ..ssh import vios_labels as ssh
from ..ssh.selectors import resolve_system_name
from ..ssh.transport import HMCCLIError


async def _system_name(config: HMCConfig, system_name_or_uuid: str) -> str:
    name = await resolve_system_name(config, system_name_or_uuid)
    if name is None:
        raise HMCCLIError(
            f"Could not resolve managed system {system_name_or_uuid!r} to an HMC CLI name"
        )
    return name


async def list_vios_fc_port_labels(
    config: HMCConfig,
    system_name_or_uuid: str,
    *,
    vios_name: str | None = None,
    vios_id: int | None = None,
) -> list[dict[str, str]]:
    return await ssh.list_vios_fc_port_labels(
        config,
        await _system_name(config, system_name_or_uuid),
        vios_name=vios_name,
        vios_id=vios_id,
    )


async def set_vios_fc_port_label(
    config: HMCConfig,
    system_name_or_uuid: str,
    label: str,
    port_name: str,
    *,
    vios_name: str | None = None,
    vios_id: int | None = None,
) -> dict[str, object]:
    return await ssh.set_vios_fc_port_label(
        config,
        await _system_name(config, system_name_or_uuid),
        label,
        port_name,
        vios_name=vios_name,
        vios_id=vios_id,
    )


async def remove_vios_fc_port_label(
    config: HMCConfig,
    system_name_or_uuid: str,
    port_name: str,
    *,
    vios_name: str | None = None,
    vios_id: int | None = None,
) -> dict[str, object]:
    return await ssh.remove_vios_fc_port_label(
        config,
        await _system_name(config, system_name_or_uuid),
        port_name,
        vios_name=vios_name,
        vios_id=vios_id,
    )


async def list_vios_vfc_group_labels(
    config: HMCConfig, system_name_or_uuid: str
) -> list[dict[str, str]]:
    return await ssh.list_vios_vfc_group_labels(
        config, await _system_name(config, system_name_or_uuid)
    )


async def create_vios_vfc_group_label(
    config: HMCConfig,
    system_name_or_uuid: str,
    label: str,
    *,
    vios_names: Sequence[str] | None = None,
    vios_ids: Sequence[int] | None = None,
) -> dict[str, object]:
    return await ssh.create_vios_vfc_group_label(
        config,
        await _system_name(config, system_name_or_uuid),
        label,
        vios_names=vios_names,
        vios_ids=vios_ids,
    )


async def update_vios_vfc_group_label(
    config: HMCConfig,
    system_name_or_uuid: str,
    label: str,
    action: ssh.ViosGroupUpdateAction,
    *,
    new_name: str | None = None,
    vios_names: Sequence[str] | None = None,
    vios_ids: Sequence[int] | None = None,
) -> dict[str, object]:
    return await ssh.update_vios_vfc_group_label(
        config,
        await _system_name(config, system_name_or_uuid),
        label,
        action,
        new_name=new_name,
        vios_names=vios_names,
        vios_ids=vios_ids,
    )


async def remove_vios_vfc_group_label(
    config: HMCConfig, system_name_or_uuid: str, label: str
) -> dict[str, object]:
    return await ssh.remove_vios_vfc_group_label(
        config, await _system_name(config, system_name_or_uuid), label
    )
