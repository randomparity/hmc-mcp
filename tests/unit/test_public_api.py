"""Contract tests for the supported reusable Python API."""

from __future__ import annotations

import hashlib
from importlib import import_module
import inspect
import json
import pkgutil
import re
import subprocess
import sys
from types import ModuleType
from typing import get_args, get_type_hints

import hmc_mcp
from hmc_mcp import api
from hmc_mcp.client_contracts import PcmClient
from hmc_mcp.client_templates import TemplatesMixin

# ADR 0029 selects "every non-underscore top-level asynchronous function" from each
# ``operations_*`` module (`docs/adr/0029-supported-reusable-python-api-contract.md:47-49`).
# A selected name may stay out of ``api.__all__`` only with a recorded justification that
# names the ADR text excluding it. The test below also rejects entries that no longer
# describe a real omission, so this mapping cannot silently accumulate dead excuses.
ADR_0029_OPERATION_EXCLUSIONS: dict[str, str] = {}


def test_public_api_exports_the_adr_inventory() -> None:
    assert api.__all__ == [
        "HMCClient",
        "AffinityAssessmentInput",
        "AffinityAssessmentResult",
        "AffinityEvidence",
        "CapturedPolicyState",
        "PolicyState",
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
        "assess_post_activation_affinity",
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
        "migrate_lpar_with_affinity_preflight",
        "run_lpm_affinity_preflight",
        "abort_lpar_migration",
        "recover_lpar_migration",
        "remote_restart_lpar",
        "LpmResult",
        "LpmAffinityPreflightRequest",
        "LpmAffinityPreflightOutcome",
        "LpmAffinityMigrationResult",
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
        "ProvisionAffinityAssessment",
        "ProvisionNetwork",
        "ProvisionStorage",
        "ProvisionResult",
        "AttachDiskResult",
        "LparResources",
        "PartitionType",
        "list_fc_ports",
        "get_lpar_memopt_score",
        "get_minimum_affinity_policy",
        "set_minimum_affinity_policy",
        "get_system_memopt_score",
        "list_lpar_memopt_scores",
        "plan_lpar_memopt_scores",
        "plan_system_memopt_score",
        "MemoptLparSelector",
        "MemoptResourceGroupSelector",
        "ResourceGroupAffinityResult",
        "MinimumAffinityPolicyResult",
        "MinimumAffinityPolicy",
        "list_resource_group_memopt_scores",
        "plan_resource_group_memopt_scores",
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
        "list_optical_mappings",
        "mount_optical_media",
        "unmount_optical_media",
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
        "LparSnapshot",
        "SnapshotInspection",
        "SnapshotValidationError",
        "capture_lpar_snapshot",
        "assess_snapshot_affinity",
        "inspect_lpar_snapshot",
        "validate_lpar_snapshot",
    ]


def _operations_modules() -> dict[str, ModuleType]:
    """Every ``hmc_mcp.operations_*`` module ADR 0029's selection rule governs."""
    return {
        f"hmc_mcp.{found.name}": import_module(f"hmc_mcp.{found.name}")
        for found in pkgutil.iter_modules(hmc_mcp.__path__)
        if found.name.startswith("operations_")
    }


def _selected_operations(modules: dict[str, ModuleType]) -> set[str]:
    """Apply ADR 0029's rule mechanically: non-underscore, top-level, coroutine.

    A coroutine an operation module merely imported is owned by the module that
    defined it, so ``__module__`` decides ownership and no name is attributed twice.
    """
    selected: set[str] = set()
    for module_name, module in modules.items():
        for name, value in vars(module).items():
            if name.startswith("_") or not inspect.iscoroutinefunction(value):
                continue
            if getattr(value, "__module__", None) == module_name:
                selected.add(name)
    return selected


def test_facade_operation_set_matches_adr_0029_selection_rule() -> None:
    modules = _operations_modules()
    selected = _selected_operations(modules)
    exported = set(api.__all__)

    unexported = selected - exported
    assert unexported == set(ADR_0029_OPERATION_EXCLUSIONS), (
        "operations the ADR 0029 rule selects but the facade omits: "
        f"{sorted(unexported - set(ADR_0029_OPERATION_EXCLUSIONS))}; "
        "exclusions naming operations that are no longer omitted: "
        f"{sorted(set(ADR_0029_OPERATION_EXCLUSIONS) - unexported)}"
    )
    unexplained = [
        name for name, reason in ADR_0029_OPERATION_EXCLUSIONS.items() if not reason
    ]
    assert unexplained == [], unexplained

    facade_operations = {
        name
        for name in exported
        if inspect.iscoroutinefunction(getattr(api, name))
        and getattr(getattr(api, name), "__module__", None) in modules
    }
    assert facade_operations == selected - set(ADR_0029_OPERATION_EXCLUSIONS)


def test_public_api_reexports_implementation_objects_directly() -> None:
    implementation = [
        module
        for name, module in list(sys.modules.items())
        if name.startswith("hmc_mcp.") and name != "hmc_mcp.api" and module is not None
    ]
    for name in api.__all__:
        value = getattr(api, name)
        assert any(
            getattr(module, name, None) is value for module in implementation
        ), name

def test_runtime_httpx_annotations_remain_resolvable() -> None:
    assert get_type_hints(PcmClient)["_http"].__module__ == "httpx"
    assert get_type_hints(PcmClient._request)["return"].__module__ == "httpx"
    assert get_type_hints(TemplatesMixin)["_http"].__module__ == "httpx"


def test_public_operations_are_async_and_signatures_are_frozen() -> None:
    """ADR 0029: the supported signatures move only with a recorded decision.

        Last moved by issue #363, which exported the drifted operations the
        ADR 0029 selection rule already covered: the optical-media operations
        ``list_optical_mappings``, ``mount_optical_media``, and
        ``unmount_optical_media``, plus ``assess_post_activation_affinity``.
        Before that, issue #320 added affinity-aware LPM preflight.
        Before that, issue #318 added post-activation affinity assessment.
        Before that, issue #316 added the Power11 minimum-affinity policy write.
        Before that, issue #315 added the Power11 minimum-affinity policy read.
        Before that, issue #312 added capability-aware resource-group affinity.
        Before that, issue #311 added read-only affinity planning operations.
        Before that, issue #310 added the LPAR memory-optimization score operations.
        Before that, issue #400 added the owning-system selector to
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
    # Moved by #363: four already-selected operations join the facade manifest.
    expected_digest = "2aaae04d6a8b2f85f39ed9762fa650ef9c108076caff1f68497fca1c12e5f2e7"  # pragma: allowlist secret
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
    forbidden = ("typer", "rich", "fastmcp")
    for name in api.__all__:
        value = getattr(api, name)
        try:
            signature = str(inspect.signature(value)).lower().replace("hmc_mcp.", "")
        except (TypeError, ValueError):
            continue
        assert not any(package in signature for package in forbidden), name
        assert re.search(r"(?<![\w-])mcp\.", signature) is None, name


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
