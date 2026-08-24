"""Contract tests for the supported reusable Python API."""

from __future__ import annotations

import hashlib
from importlib import import_module
import inspect
import json
import subprocess
import sys
from typing import get_args, get_type_hints

from hmc_mcp import api
from hmc_mcp.client_contracts import PcmClient
from hmc_mcp.client_templates import TemplatesMixin


def test_public_api_exports_the_adr_inventory() -> None:
    assert api.__all__ == [
        "HMCClient",
        "HMCConfig",
        "ConfigError",
        "load_profile",
        "HMCError",
        "HMCTransportError",
        "HMCCLIError",
        "list_adapters",
        "add_network_adapter",
        "add_vios_adapter",
        "delete_adapter",
        "AdapterResult",
        "AdapterType",
        "capacity_report",
        "find_placement",
        "lpar_summary",
        "system_summary",
        "decommission_lpar",
        "DecommissionResult",
        "fleet_health",
        "FleetHealthResult",
        "authorize_decommission_lpar_ownership_snapshot",
        "authorize_lpar_mutation",
        "resolve_lpar_ownership_names",
        "list_lpar_ownership",
        "stamp_created_lpar_ownership",
        "create_and_stamp_lpar",
        "set_lpar_ownership_description",
        "delete_lpar",
        "power_lpar",
        "rename_lpar",
        "LparCreation",
        "LparCreationResult",
        "LparPowerResult",
        "read_lpar_boot_order",
        "set_lpar_boot_order",
        "clear_lpar_boot_order",
        "BootDeviceSelector",
        "migrate_lpar",
        "abort_lpar_migration",
        "recover_lpar_migration",
        "remote_restart_lpar",
        "LpmResult",
        "list_virtual_switches",
        "list_virtual_networks",
        "create_virtual_network",
        "delete_virtual_network",
        "list_network_bridges",
        "resolve_pcm_resource",
        "get_pcm_preferences",
        "set_pcm_preferences",
        "metric_links",
        "metric_data",
        "PcmCategory",
        "MetricKind",
        "DedicatedSlot",
        "InventoryResult",
        "InventorySelector",
        "PcieAssignmentUnavailableError",
        "SriovAdapter",
        "SriovLogicalPort",
        "SriovPhysicalPort",
        "assign_dedicated_pcie_slot",
        "list_dedicated_slots",
        "list_sriov_adapters",
        "list_sriov_logical_ports",
        "list_sriov_physical_ports",
        "unassign_dedicated_pcie_slot",
        "SriovLogicalPortCapabilityError",
        "SriovLogicalPortChangeResult",
        "SriovLogicalPortPartialError",
        "SriovLogicalPortSnapshot",
        "assign_sriov_logical_port",
        "set_sriov_adapter_mode",
        "unassign_sriov_logical_port",
        "attach_disk_to_lpar",
        "provision_lpar",
        "ProvisionNetwork",
        "ProvisionStorage",
        "ProvisionResult",
        "AttachDiskResult",
        "LparResources",
        "PartitionType",
        "list_fc_ports",
        "get_lpar_memopt_score",
        "list_lpar_memopt_scores",
        "list_sea_adapters",
        "set_sriov_adapter_mode",
        "list_vnics",
        "VnicBackingSelector",
        "VnicBackingSnapshot",
        "VnicSnapshot",
        "VnicChangeResult",
        "VnicCapabilityError",
        "VnicPartialError",
        "add_vnic",
        "remove_vnic",
        "SriovMode",
        "AssignmentResult",
        "AssignmentStep",
        "DedicatedPcieAssignment",
        "LparPcieAssignments",
        "LparPcieWorkflowResult",
        "SriovLogicalPortAssignment",
        "VnicAssignment",
        "apply_lpar_pcie_assignments",
        "prevalidate_lpar_pcie_assignments",
        "list_volume_groups",
        "create_volume_group",
        "create_virtual_disk",
        "delete_virtual_disk",
        "map_storage",
        "upload_iso",
        "create_media_repository",
        "create_optical_media",
        "delete_media_repository",
        "delete_optical_media",
        "get_media_repository",
        "list_optical_media",
        "list_storage_mappings",
        "detach_storage_mapping",
        "create_logical_unit",
        "delete_logical_unit",
        "StorageKind",
        "LuType",
        "DeviceType",
        "power_system",
        "list_partition_templates",
        "get_partition_template",
        "deploy_partition_template",
        "power_vios",
        "capture_lpar_console",
        "ConsoleCapture",
        "ConsoleHeldError",
    ]


def test_public_api_reexports_implementation_objects_directly() -> None:
    sources = {
        "hmc_mcp.client": {"HMCClient"},
        "hmc_mcp.client_adapters": {"AdapterType"},
        "hmc_mcp.config": {"ConfigError", "HMCConfig", "load_profile"},
        "hmc_mcp.documents": {
            "BootDeviceSelector",
            "LparResources",
            "PartitionType",
            "StorageKind",
        },
        "hmc_mcp.errors": {"HMCError", "HMCTransportError"},
        "hmc_mcp.jobs": {"DeviceType", "LuType"},
        "hmc_mcp.operations_adapters": {
            "AdapterResult",
            "add_network_adapter",
            "add_vios_adapter",
            "delete_adapter",
            "list_adapters",
        },
        "hmc_mcp.operations_capacity": {"capacity_report", "find_placement"},
        "hmc_mcp.operations_assignments": {
            "AssignmentResult",
            "AssignmentStep",
            "DedicatedPcieAssignment",
            "LparPcieAssignments",
            "LparPcieWorkflowResult",
            "SriovLogicalPortAssignment",
            "VnicAssignment",
            "apply_lpar_pcie_assignments",
            "prevalidate_lpar_pcie_assignments",
        },
        "hmc_mcp.operations_composite": {"lpar_summary", "system_summary"},
        "hmc_mcp.operations_decommission": {
            "DecommissionResult",
            "decommission_lpar",
        },
        "hmc_mcp.operations_health": {"FleetHealthResult", "fleet_health"},
        "hmc_mcp.operations_lpar": {
            "LparCreation",
            "LparCreationResult",
            "LparPowerResult",
            "authorize_decommission_lpar_ownership_snapshot",
            "authorize_lpar_mutation",
            "create_and_stamp_lpar",
            "clear_lpar_boot_order",
            "delete_lpar",
            "power_lpar",
            "read_lpar_boot_order",
            "rename_lpar",
            "resolve_lpar_ownership_names",
            "list_lpar_ownership",
            "set_lpar_boot_order",
            "stamp_created_lpar_ownership",
            "set_lpar_ownership_description",
        },
        "hmc_mcp.operations_lpm": {
            "LpmResult",
            "abort_lpar_migration",
            "migrate_lpar",
            "recover_lpar_migration",
            "remote_restart_lpar",
        },
        "hmc_mcp.operations_network": {
            "create_virtual_network",
            "delete_virtual_network",
            "list_network_bridges",
            "list_virtual_networks",
            "list_virtual_switches",
        },
        "hmc_mcp.operations_pcm": {
            "MetricKind",
            "PcmCategory",
            "get_pcm_preferences",
            "metric_data",
            "metric_links",
            "resolve_pcm_resource",
            "set_pcm_preferences",
        },
        "hmc_mcp.operations_pcie": {
            "DedicatedSlot",
            "InventoryResult",
            "InventorySelector",
            "PcieAssignmentUnavailableError",
            "SriovAdapter",
            "SriovLogicalPort",
            "SriovPhysicalPort",
            "assign_dedicated_pcie_slot",
            "list_dedicated_slots",
            "list_sriov_adapters",
            "list_sriov_logical_ports",
            "list_sriov_physical_ports",
            "unassign_dedicated_pcie_slot",
            "SriovLogicalPortCapabilityError",
            "SriovLogicalPortChangeResult",
            "SriovLogicalPortPartialError",
            "SriovLogicalPortSnapshot",
            "assign_sriov_logical_port",
            "set_sriov_adapter_mode",
            "unassign_sriov_logical_port",
        },
        "hmc_mcp.operations_provision": {
            "AttachDiskResult",
            "ProvisionNetwork",
            "ProvisionResult",
            "ProvisionStorage",
            "attach_disk_to_lpar",
            "provision_lpar",
        },
        "hmc_mcp.operations_ssh_network": {
            "VnicBackingSelector",
            "VnicBackingSnapshot",
            "VnicSnapshot",
            "VnicChangeResult",
            "VnicCapabilityError",
            "VnicPartialError",
            "add_vnic",
            "get_lpar_memopt_score",
            "list_fc_ports",
            "list_lpar_memopt_scores",
            "list_sea_adapters",
            "list_vnics",
            "remove_vnic",
        },
        "hmc_mcp.operations_storage": {
            "create_logical_unit",
            "create_media_repository",
            "create_optical_media",
            "create_virtual_disk",
            "create_volume_group",
            "delete_logical_unit",
            "delete_media_repository",
            "delete_optical_media",
            "delete_virtual_disk",
            "detach_storage_mapping",
            "get_media_repository",
            "list_optical_media",
            "list_storage_mappings",
            "list_volume_groups",
            "map_storage",
            "upload_iso",
        },
        "hmc_mcp.operations_systems": {"power_system"},
        "hmc_mcp.operations_templates": {
            "deploy_partition_template",
            "get_partition_template",
            "list_partition_templates",
        },
        "hmc_mcp.operations_vios": {"power_vios"},
        "hmc_mcp.console_capture": {
            "capture_lpar_console",
            "ConsoleCapture",
            "ConsoleHeldError",
        },
        "hmc_mcp.ssh": {"HMCCLIError"},
        "hmc_mcp.ssh_commands": {"SriovMode"},
    }
    tested = set()
    for module_name, names in sources.items():
        module = import_module(module_name)
        for name in names:
            assert getattr(api, name) is getattr(module, name)
        tested.update(names)
    assert tested == set(api.__all__)


def test_runtime_httpx_annotations_remain_resolvable() -> None:
    assert get_type_hints(PcmClient)["_http"].__module__ == "httpx"
    assert get_type_hints(PcmClient._request)["return"].__module__ == "httpx"
    assert get_type_hints(TemplatesMixin)["_http"].__module__ == "httpx"


def test_public_operations_are_async_and_signatures_are_frozen() -> None:
    """ADR 0029: the supported signatures move only with a recorded decision.

        Last moved by issue #310, which added the LPAR memory-optimization
        score operations. Before that, issue #400 added the owning-system selector to
        logical-partition PCM metric operations (ADR 0077). Before that, issue
        #401 made the destructive RemoteRestart
        operation and source-system selector explicit (ADR 0078). Before that,
        issue #385 added the ``capture_lpar_console``
    operation, the ``ConsoleCapture`` result model, and the
    ``ConsoleHeldError`` contention error (ADR 0072). Before that, #375
    added the ``list_lpar_ownership`` operation (bulk per-system LPAR
    ownership read; ADR 0071). Before that, ADR 0067 added the
    ``stamp_policy`` field to ``LparCreation`` (issue #377), and before that
    ADR 0066 added ``set_lpar_ownership_description`` (issue #376), and
    before it ADR 0064 added the optional ``caller_token`` parameter to
    ``provision_lpar``. Before that, ADR 0059 changed ``HMCConfig.port``'s
    default from 12443 to 443. ADR 0058 added declarative LPAR PCIe
    assignments, and ADR 0054 added the normalized PCIe inventory models and
    operations. Before that, ADR 0050 added
    ``HMCConfig.iso_url_allowlist`` — a pydantic model's ``__init__``
    signature is derived from its fields, so a new setting moves the digest
    even though no operation's parameters changed. Before that, ADR 0049
    narrowed ``upload_iso``'s ``iso_source`` from ``str | Path`` to ``str``.
    """
    operations = {
        name: getattr(api, name)
        for name in api.__all__
        if inspect.isfunction(getattr(api, name)) and name != "load_profile"
    }
    assert all(
        inspect.iscoroutinefunction(operation) for operation in operations.values()
    )

    signatures = {}
    for name in api.__all__:
        try:
            signatures[name] = str(inspect.signature(getattr(api, name)))
        except (TypeError, ValueError):
            continue
    encoded = json.dumps(signatures, sort_keys=True, separators=(",", ":")).encode()
    # Moved by #310: current LPAR memory-optimization scores are reusable API
    # operations under ADR 0029.
    expected_digest = "41344e8e8218937deef3428971c44c1d39c3c7c937843dd6dab56ab4a6682fca"  # pragma: allowlist secret
    assert hashlib.sha256(encoded).hexdigest() == expected_digest


def test_public_error_hierarchy_is_frozen() -> None:
    assert issubclass(api.HMCTransportError, api.HMCError)
    assert issubclass(api.HMCCLIError, api.HMCError)
    assert issubclass(api.ConfigError, ValueError)


def test_hmc_client_supported_lifecycle_members_are_present() -> None:
    supported = {
        "__init__",
        "__aenter__",
        "__aexit__",
        "is_logged_on",
        "logon",
        "logoff",
    }
    assert {name for name in supported if hasattr(api.HMCClient, name)} == supported


def test_exported_literal_value_sets_are_frozen() -> None:
    assert get_args(api.AdapterType) == (
        "ClientNetworkAdapter",
        "VirtualSCSIClientAdapter",
        "VirtualFibreChannelClientAdapter",
        "VirtualNICDedicated",
    )
    assert get_args(api.PartitionType) == (
        "AIX/Linux",
        "OS400",
        "Virtual IO Server",
    )
    assert get_args(api.StorageKind) == ("PhysicalVolume", "VirtualDisk")
    assert get_args(api.DeviceType) == ("VirtualIO_Disk", "VirtualIO_Image")
    assert get_args(api.LuType) == ("THIN", "THICK")
    assert get_args(api.MetricKind) == ("processed", "aggregated")
    assert get_args(api.PcmCategory) == ("ManagedSystem", "LogicalPartition")
    assert get_args(api.SriovMode) == ("sriov", "dedicated")


def test_public_signatures_exclude_presentation_types() -> None:
    forbidden = ("typer", "rich", "fastmcp", "mcp.")
    for name in api.__all__:
        value = getattr(api, name)
        try:
            signature = str(inspect.signature(value)).lower()
        except (TypeError, ValueError):
            continue
        assert not any(package in signature for package in forbidden), name


def test_importing_public_api_does_not_import_presentation_modules() -> None:
    script = """
import sys
import hmc_mcp.api

loaded = sorted(
    name for name in sys.modules
    if name.split('.', 1)[0] in {'fastmcp', 'mcp', 'rich', 'typer'}
    or name == 'hmc_mcp._app'
    or name == 'hmc_mcp.cli'
    or name.startswith('hmc_mcp.cli_')
    or name == 'hmc_mcp.server'
    or name.startswith('hmc_mcp.server_')
)
assert loaded == [], loaded
"""
    subprocess.run([sys.executable, "-c", script], check=True)
