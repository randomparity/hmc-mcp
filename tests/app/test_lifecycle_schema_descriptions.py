"""Rendered-schema contracts for the core lifecycle tools."""

from __future__ import annotations

import asyncio

from hmc_mcp.server import mcp


SCOPED_TOOLS = {
    "hmc_console_info",
    "hmc_list_configured_hosts",
    "hmc_list_systems",
    "hmc_list_lpars",
    "hmc_get_lpar",
    "hmc_get_lpar_state",
    "hmc_list_vios",
    "hmc_get_vios",
    "hmc_list_resources",
    "hmc_get_system",
    "hmc_modify_system",
    "hmc_power_on_system",
    "hmc_power_off_system",
    "hmc_create_lpar",
    "hmc_modify_lpar",
    "hmc_rename_lpar",
    "hmc_dlpar_proc",
    "hmc_dlpar_mem",
    "hmc_delete_lpar",
    "hmc_power_on_lpar",
    "hmc_power_off_lpar",
    "hmc_create_vios",
    "hmc_delete_vios",
    "hmc_install_vios",
    "hmc_install_lpar_os",
    "hmc_list_vios_backups",
    "hmc_backup_vios",
    "hmc_restore_vios",
    "hmc_power_on_vios",
    "hmc_power_off_vios",
    "hmc_get_job",
    "hmc_list_recent_jobs",
    "hmc_wait_for_job",
    "hmc_lpar_summary",
    "hmc_system_summary",
    "hmc_provision_lpar",
    "hmc_capacity_report",
    "hmc_find_placement",
    "hmc_migrate_lpar",
    "hmc_migrate_validate_lpar",
    "hmc_migrate_abort_lpar",
    "hmc_migrate_recover_lpar",
    "hmc_remote_restart_lpar",
}


def _tools_by_name():
    return {tool.name: tool for tool in asyncio.run(mcp.list_tools())}


def test_core_lifecycle_parameters_have_rendered_descriptions():
    tools = _tools_by_name()
    assert SCOPED_TOOLS <= tools.keys()
    missing = {
        (name, parameter)
        for name in SCOPED_TOOLS
        for parameter, schema in tools[name].parameters.get("properties", {}).items()
        if not schema.get("description", "").strip()
    }
    assert missing == set()


def test_nested_lifecycle_parameters_have_rendered_descriptions():
    tools = _tools_by_name()
    nested = {
        "resources": tools["hmc_create_lpar"].parameters["properties"]["resources"],
        "network": tools["hmc_provision_lpar"].parameters["properties"]["network"],
        "storage": tools["hmc_provision_lpar"].parameters["properties"]["storage"],
    }
    missing = {
        (group, field)
        for group, schema in nested.items()
        for field, field_schema in schema["properties"].items()
        if not field_schema.get("description", "").strip()
    }
    assert missing == set()


def test_high_risk_lifecycle_guidance_is_rendered():
    tools = _tools_by_name()
    wait_description = tools["hmc_wait_for_job"].description
    for status in (
        "CANCELED_BEFORE_START",
        "CANCELED_WHILE_RUNNING",
        "COMPLETED",
        "COMPLETED_OK",
        "COMPLETED_WITH_ERROR",
        "COMPLETED_WITH_WARNINGS",
        "EXCEPTION",
        "FAILED",
        "FAILED_BEFORE_COMPLETION",
        "FAILED_BEFORE_COMPLETION_RETRY",
        "FAILED_TO_START",
    ):
        assert status in wait_description

    rename = tools["hmc_rename_lpar"]
    assert "ADR 0011" in rename.description
    assert (
        "explicit operator approval"
        in rename.parameters["properties"]["ownership_override"]["description"]
    )

    provision = tools["hmc_provision_lpar"].output_schema["properties"]
    for field in (
        "resource_created",
        "workflow_completed",
        "lpar_uuid",
        "dry_run",
        "ownership_stamped",
        "steps",
        "warnings",
    ):
        assert provision[field]["description"].strip()
