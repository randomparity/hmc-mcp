"""MCP tools for bounded VIOS Fibre Channel label administration."""

from __future__ import annotations

from collections.abc import Sequence

from .._app import with_config
from ..operations import vios_labels as operations
from ..ssh.vios_labels import ViosGroupUpdateAction
from ..tool_registry import tool_module

tool, register_tools, tool_security = tool_module()


@tool(effect="read", operation="vios_label.list_fc_ports", target_kind="managed_system")
def hmc_list_vios_fc_port_labels(
    system_name_or_uuid: str,
    vios_name: str | None = None,
    vios_id: int | None = None,
    profile: str | None = None,
) -> list[dict[str, str]]:
    """List FC-port labels used for migration placement without changing adapters.

    Args:
        system_name_or_uuid: Managed-system name or UUID.
        vios_name: Optional VIOS partition name used to filter the ports.
        vios_id: Optional VIOS partition ID used to filter the ports.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    return with_config(
        lambda config: operations.list_vios_fc_port_labels(
            config, system_name_or_uuid, vios_name=vios_name, vios_id=vios_id
        ),
        profile=profile,
    )


@tool(effect="mutate", operation="vios_label.set_fc_port", target_kind="managed_system")
def hmc_set_vios_fc_port_label(
    system_name_or_uuid: str,
    label: str,
    port_name: str,
    vios_name: str | None = None,
    vios_id: int | None = None,
    profile: str | None = None,
) -> dict[str, object]:
    """Set one FC-port migration label without adding or deleting adapters.

    Args:
        system_name_or_uuid: Managed-system name or UUID.
        label: New label for the selected FC port.
        port_name: Physical FC port name, such as ``fcs0``.
        vios_name: VIOS partition name; mutually exclusive with ``vios_id``.
        vios_id: VIOS partition ID; mutually exclusive with ``vios_name``.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    return with_config(
        lambda config: operations.set_vios_fc_port_label(
            config,
            system_name_or_uuid,
            label,
            port_name,
            vios_name=vios_name,
            vios_id=vios_id,
        ),
        profile=profile,
    )


@tool(
    effect="destructive",
    operation="vios_label.remove_fc_port",
    target_kind="managed_system",
)
def hmc_remove_vios_fc_port_label(
    system_name_or_uuid: str,
    port_name: str,
    vios_name: str | None = None,
    vios_id: int | None = None,
    profile: str | None = None,
) -> dict[str, object]:
    """Remove one FC-port migration label without deleting the FC adapter.

    Args:
        system_name_or_uuid: Managed-system name or UUID.
        port_name: Physical FC port name, such as ``fcs0``.
        vios_name: VIOS partition name; mutually exclusive with ``vios_id``.
        vios_id: VIOS partition ID; mutually exclusive with ``vios_name``.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    return with_config(
        lambda config: operations.remove_vios_fc_port_label(
            config,
            system_name_or_uuid,
            port_name,
            vios_name=vios_name,
            vios_id=vios_id,
        ),
        profile=profile,
    )


@tool(
    effect="read", operation="vios_label.list_vfc_groups", target_kind="managed_system"
)
def hmc_list_vios_vfc_group_labels(
    system_name_or_uuid: str, profile: str | None = None
) -> list[dict[str, str]]:
    """List vFC group labels that guide migration and remote-restart placement.

    Args:
        system_name_or_uuid: Managed-system name or UUID.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    return with_config(
        lambda config: operations.list_vios_vfc_group_labels(
            config, system_name_or_uuid
        ),
        profile=profile,
    )


@tool(
    effect="mutate",
    operation="vios_label.create_vfc_group",
    target_kind="managed_system",
)
def hmc_create_vios_vfc_group_label(
    system_name_or_uuid: str,
    label: str,
    vios_names: Sequence[str] | None = None,
    vios_ids: Sequence[int] | None = None,
    profile: str | None = None,
) -> dict[str, object]:
    """Create one vFC placement group without adding or deleting adapters.

    Args:
        system_name_or_uuid: Managed-system name or UUID.
        label: New vFC group label.
        vios_names: Non-empty VIOS name list; mutually exclusive with ``vios_ids``.
        vios_ids: Non-empty VIOS ID list; mutually exclusive with ``vios_names``.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    return with_config(
        lambda config: operations.create_vios_vfc_group_label(
            config,
            system_name_or_uuid,
            label,
            vios_names=vios_names,
            vios_ids=vios_ids,
        ),
        profile=profile,
    )


@tool(
    effect="mutate",
    operation="vios_label.update_vfc_group",
    target_kind="managed_system",
)
def hmc_update_vios_vfc_group_label(
    system_name_or_uuid: str,
    label: str,
    action: ViosGroupUpdateAction,
    new_name: str | None = None,
    vios_names: Sequence[str] | None = None,
    vios_ids: Sequence[int] | None = None,
    profile: str | None = None,
) -> dict[str, object]:
    """Rename or change membership of one vFC placement group.

    Args:
        system_name_or_uuid: Managed-system name or UUID.
        label: Existing vFC group label.
        action: Rename, add-members, or remove-members operation.
        new_name: Replacement label required only for rename.
        vios_names: VIOS names required for a name-based membership change.
        vios_ids: VIOS IDs required for an ID-based membership change.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    return with_config(
        lambda config: operations.update_vios_vfc_group_label(
            config,
            system_name_or_uuid,
            label,
            action,
            new_name=new_name,
            vios_names=vios_names,
            vios_ids=vios_ids,
        ),
        profile=profile,
    )


@tool(
    effect="destructive",
    operation="vios_label.remove_vfc_group",
    target_kind="managed_system",
)
def hmc_remove_vios_vfc_group_label(
    system_name_or_uuid: str, label: str, profile: str | None = None
) -> dict[str, object]:
    """Remove one named vFC placement group without deleting adapters.

    Args:
        system_name_or_uuid: Managed-system name or UUID.
        label: Existing vFC group label to remove.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    return with_config(
        lambda config: operations.remove_vios_vfc_group_label(
            config, system_name_or_uuid, label
        ),
        profile=profile,
    )
