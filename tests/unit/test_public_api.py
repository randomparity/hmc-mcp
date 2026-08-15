"""Contract tests for the supported reusable Python API."""

from __future__ import annotations

import hashlib
import inspect
import json
import subprocess
import sys
from typing import get_args

from hmc_mcp import api


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
        "stamp_created_lpar_ownership",
        "create_and_stamp_lpar",
        "delete_lpar",
        "power_lpar",
        "rename_lpar",
        "LparCreation",
        "LparCreationResult",
        "LparPowerResult",
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
        "attach_disk_to_lpar",
        "provision_lpar",
        "ProvisionNetwork",
        "ProvisionStorage",
        "ProvisionResult",
        "AttachDiskResult",
        "LparResources",
        "PartitionType",
        "list_fc_ports",
        "list_sea_adapters",
        "set_sriov_adapter_mode",
        "list_vnics",
        "add_vnic",
        "remove_vnic",
        "SriovMode",
        "list_volume_groups",
        "create_volume_group",
        "create_virtual_disk",
        "map_storage",
        "create_media_repository",
        "create_optical_media",
        "delete_media_repository",
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
    ]


def test_public_api_reexports_implementation_objects_directly() -> None:
    assert all(getattr(api, name).__module__ != api.__name__ for name in api.__all__)


def test_public_operations_are_async_and_signatures_are_frozen() -> None:
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
    expected_digest = (
        "7adf6de07e33f94022d59bb5cb828244"  # pragma: allowlist secret
        "e9eedd7399e3c101354b59b5e6745665"  # pragma: allowlist secret
    )
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
