"""Exhaustive contract tests for the live tool security classification.

Every registered MCP tool must carry one authoritative ToolSecurity record, and
the MCP annotations shipped to clients must be derived from it. These tests fail
when a tool omits the metadata, contradicts its handler, or silently changes
classification. See docs/adr/0035-enforceable-tool-security-metadata.md.
"""

from __future__ import annotations

import asyncio

import pytest

from hmc_mcp import server_command
from hmc_mcp.server import TOOL_SECURITY, mcp
from hmc_mcp.tool_registry import (
    EFFECTS,
    REQUIRED_TARGET_ARGUMENTS,
    annotations_for,
    validate_security,
)

# The classification as it stood before ADR 0035, transcribed from _app.py at
# 20f3068. A frozen regression snapshot: no tool is ever added here.
LEGACY_READ_ONLY = frozenset(
    {
        "hmc_console_info",
        "hmc_list_systems",
        "hmc_system_summary",
        "hmc_list_lpars",
        "hmc_get_lpar",
        "hmc_get_lpar_state",
        "hmc_lpar_summary",
        "hmc_list_vios",
        "hmc_get_vios",
        "hmc_list_resources",
        "hmc_get_job",
        "hmc_list_recent_jobs",
        "hmc_fleet_health",
        "hmc_capacity_report",
        "hmc_find_placement",
        "hmc_get_system",
        "hmc_wait_for_job",
        "hmc_list_adapters",
        "hmc_list_configured_hosts",
        "hmc_list_volume_groups",
        "hmc_list_virtual_switches",
        "hmc_list_virtual_networks",
        "hmc_list_network_bridges",
        "hmc_list_fc_ports",
        "hmc_list_sea_adapters",
        "hmc_list_partition_templates",
        "hmc_get_partition_template",
        "hmc_list_clusters",
        "hmc_list_shared_storage_pools",
        "hmc_get_shared_storage_pool",
        "hmc_get_pcm_preferences",
        "hmc_processed_metrics",
        "hmc_processed_metric_links",
        "hmc_aggregated_metrics",
        "hmc_aggregated_metric_links",
        "hmc_list_users",
        "hmc_get_user",
        "hmc_list_password_policies",
        "hmc_list_password_policy_status",
        "hmc_get_ldap_config",
        "hmc_get_available_hmc_ptfs",
        "hmc_list_vios_backups",
        "hmc_get_lpar_description",
        "hmc_get_lpar_msp",
        "hmc_get_proc_compat_modes",
        "hmc_get_lpar_proc_compat",
        "hmc_list_io_slots",
        "hmc_list_memory_pools",
        "hmc_list_vnics",
        "hmc_get_media_repository",
        "hmc_list_optical_media",
        "hmc_list_optical_mappings",
        "hmc_list_storage_mappings",
    }
)

LEGACY_DESTRUCTIVE = frozenset(
    {
        "hmc_power_off_lpar",
        "hmc_delete_lpar",
        "hmc_decommission_lpar",
        "hmc_delete_vios",
        "hmc_delete_adapter",
        "hmc_delete_virtual_network",
        "hmc_delete_media_repository",
        "hmc_delete_optical_media",
        "hmc_delete_virtual_disk",
        "hmc_delete_logical_unit",
        "hmc_delete_user",
        "hmc_delete_password_policy",
        "hmc_remove_ldap_config",
        "hmc_remove_memory_pool",
        "hmc_remove_vnic",
        "hmc_power_off_system",
        "hmc_power_off_vios",
        "hmc_migrate_abort_lpar",
        "hmc_remote_restart_lpar",
        "hmc_restore_vios",
        "hmc_restore_lpar_profiles",
        "hmc_backup_lpar_profiles",
        "hmc_sync_lpar_profile",
        "hmc_unmount_optical_media",
        "hmc_detach_optical_mapping",
        "hmc_detach_storage_mapping",
    }
)


def _tools_by_name(enable_arbitrary_command: bool = False):
    asyncio.run(
        server_command.configure_arbitrary_command_tool(enable_arbitrary_command, mcp)
    )
    try:
        return {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    finally:
        if enable_arbitrary_command:
            asyncio.run(server_command.configure_arbitrary_command_tool(False, mcp))


def test_every_live_tool_has_security_metadata():
    """G1: no live tool escapes the classification, toggle on or off."""
    assert set(_tools_by_name()) <= set(TOOL_SECURITY)
    assert set(_tools_by_name(True)) == set(TOOL_SECURITY)
    assert "hmc_run_command" in TOOL_SECURITY


def test_annotations_are_derived_from_the_effect_class():
    """G4: the shipped hint is a function of the declared effect, nothing else."""
    for name, tool in _tools_by_name(True).items():
        assert tool.annotations == annotations_for(TOOL_SECURITY[name].effect), name


def test_declared_effects_use_the_closed_vocabulary():
    for name, security in TOOL_SECURITY.items():
        assert security.effect in EFFECTS, name


def test_selectors_and_connection_arguments_are_public_parameters():
    """G3: every declared selector is really an argument a caller supplies."""
    for name, tool in _tools_by_name(True).items():
        security = TOOL_SECURITY[name]
        properties = set(tool.parameters.get("properties", {}))
        for target in security.targets:
            assert target.argument in properties, (name, target.argument)
        if security.connection_argument is not None:
            assert security.connection_argument in properties, name


@pytest.mark.parametrize(
    "tool_name, argument, expected_required",
    [
        ("hmc_power_off_lpar", "system_name_or_uuid", False),
        ("hmc_power_off_vios", "system_name_or_uuid", False),
        ("hmc_delete_vios", "system_name_or_uuid", False),
        ("hmc_restore_vios", "system_name_or_uuid", False),
        ("hmc_list_lpars", "system_name_or_uuid", False),
        ("hmc_delete_lpar", "lpar_name_or_uuid", True),
        ("hmc_migrate_lpar", "target_system_name_or_uuid", True),
    ],
)
def test_optional_selectors_are_marked_optional(tool_name, argument, expected_required):
    """G3 anchors: #223 must be able to tell an omitted selector from a required one."""
    selector = next(
        target
        for target in TOOL_SECURITY[tool_name].targets
        if target.argument == argument
    )
    assert selector.required is expected_required


def test_multi_kind_tools_declare_every_target():
    """G6: the omission the argument table exists to make impossible."""
    migrate = {(t.kind, t.argument) for t in TOOL_SECURITY["hmc_migrate_lpar"].targets}
    assert ("lpar", "lpar_name_or_uuid") in migrate
    assert ("managed_system", "target_system_name_or_uuid") in migrate

    attach = {
        (t.kind, t.argument) for t in TOOL_SECURITY["hmc_attach_disk_to_lpar"].targets
    }
    assert ("lpar", "lpar_name_or_uuid") in attach
    assert ("vios", "vios_uuid") in attach


def test_target_declarations_are_internally_consistent():
    """G6: V7 and V8 hold across the whole live registry."""
    for name in _tools_by_name(True):
        security = TOOL_SECURITY[name]
        if security.target_kind == "none":
            assert security.targets == (), name
            assert security.connection_argument is None, name
            continue
        if security.target_kind != "console":
            assert any(t.kind == security.target_kind for t in security.targets), name
        arguments = [t.argument for t in security.targets]
        assert len(arguments) == len(set(arguments)), name


def test_every_table_argument_becomes_a_target():
    """G6: a handler taking an identity argument cannot silently drop it."""
    for name, tool in _tools_by_name(True).items():
        declared = {t.argument for t in TOOL_SECURITY[name].targets}
        expected = set(tool.parameters.get("properties", {})) & set(
            REQUIRED_TARGET_ARGUMENTS
        )
        assert expected <= declared, (name, sorted(expected - declared))


def test_operation_identities_are_unique():
    """G2: two tools may not claim one operation."""
    operations = [security.operation for security in TOOL_SECURITY.values()]
    assert len(operations) == len(set(operations))


def test_delete_and_remove_tools_are_destructive():
    """G7: defence in depth against the likeliest misclassification."""
    for name, security in TOOL_SECURITY.items():
        if name.startswith(("hmc_delete_", "hmc_remove_")):
            assert security.effect == "destructive", name


def test_arbitrary_command_is_absent_by_default_and_maximally_classified():
    """G8: the escape hatch is off by default and is the only arbitrary command."""
    default = _tools_by_name()
    assert "hmc_run_command" not in default
    assert not [n for n in default if TOOL_SECURITY[n].effect == "arbitrary-command"]

    enabled = _tools_by_name(True)
    assert [n for n in enabled if TOOL_SECURITY[n].effect == "arbitrary-command"] == [
        "hmc_run_command"
    ]
    validate_security(
        server_command.HMC_RUN_COMMAND_SECURITY, server_command.hmc_run_command
    )


def test_only_the_local_config_tool_opens_no_hmc_connection():
    """G10: every tool that reaches an HMC declares how its connection is chosen."""
    for name, security in TOOL_SECURITY.items():
        if name == "hmc_list_configured_hosts":
            assert security.target_kind == "none", name
            assert security.connection_argument is None, name
        else:
            assert security.connection_argument == "profile", name


def test_no_classification_regresses_against_the_pre_adr_sets():
    """G11: a permutation-proof pin on the classification this change inherited."""
    for name in LEGACY_READ_ONLY:
        assert TOOL_SECURITY[name].effect == "read", name
    for name in LEGACY_DESTRUCTIVE:
        assert TOOL_SECURITY[name].effect == "destructive", name
    assert TOOL_SECURITY["hmc_read_lpar_boot_order"].effect == "read"


def test_legacy_classification_sets_are_gone():
    """G9: replace, don't deprecate."""
    from hmc_mcp import _app, server

    for module in (_app, server):
        for removed in (
            "READ_ONLY_TOOLS",
            "DESTRUCTIVE_TOOLS",
            "_READ_ONLY",
            "_DESTRUCTIVE",
            "_STATE_CHANGING",
        ):
            assert not hasattr(module, removed), f"{module.__name__}.{removed}"
