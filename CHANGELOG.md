# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project
pre-1.0, so versions follow ADR 0029 (`docs/adr/0029-supported-reusable-python-api-contract.md`):
any change to the facade manifest — adding, removing, or renaming an export of
`hmc_mcp.api`, or changing an exported enum member or literal alternative — requires a minor
release during `0.x`.

## Convention: the Facade manifest section is mandatory

Every release entry below **must** contain a `### Facade manifest` section, even when nothing
moved. An entry whose manifest section says "no change to `hmc_mcp.api.__all__`" converts silence
into a positive statement for consumers deciding whether an upgrade can break them. Where the
manifest changed, the section names every added, removed, and renamed export, and every changed
exported enum member or literal alternative.

A metadata test (`tests/unit/test_changelog.py`) enforces both halves of this contract: the
version declared in `pyproject.toml` must have a matching entry, and every release entry must
carry a `### Facade manifest` section.

## [Unreleased]

### Added

- `set_lpar_ownership_description` operation and facade export (#376, ADR 0066).
- Strict LPAR ownership stamping (#377, ADR 0067): `provision_lpar` accepts a new
  `stamp_policy` field on `LparCreation` with literal alternatives `"best-effort"` (default) and
  `"required"`.
- Audit sink emits a `tls-verification-disabled` event when TLS verification is turned off via
  `HMC_INSECURE_TLS` (#379).

### Changed

- `HMC_AGENT_ID` values containing double quotes or backslashes are rejected at config load
  instead of being passed through into SSH command construction (#386).
- `hmc_install_lpar_os` and `hmc_install_vios` now drive the HMC CLI
  `installios` command over SSH (submit-and-detach: they return the remote PID
  and log path instead of a job, and `hmc_get_job`/`hmc_wait_for_job` do not
  apply). The targeted `InstallLPAR`/`InstallVIOS` REST jobs do not exist on
  any surveyed HMC (ADR 0069). Parameter changes: `nim_ip` is removed (under
  CLI semantics the HMC itself serves the install image); a required
  `install_source` (`-d`) and `system_name_or_uuid` (`-s`) replace it, and a
  required `profile_name` (`-r`, default `"default"`) plus optional
  `mac_address` (`-m`) join; `wait`/`wait_timeout_seconds`/`poll_interval`/
  `hmc_timeout_minutes` are removed because there is no job to poll (#410,
  ADR 0070).

### Documentation

- ADR 0069 records the live-HMC survey finding that the HMC REST API does not advertise the
  `InstallLPAR`/`InstallVIOS` jobs at any surveyed firmware level (#381); the disposition of the
  affected tools is tracked in #410. No code change.
- ADR 0070 records the operator decision to bridge the install tools to the
  HMC CLI `installios` command, the grammar mapping with sources, the
  submit-and-detach semantics, and the injection-validation approach (#410).

### Facade manifest

- Added: `set_lpar_ownership_description`.
- Removed: none.
- Renamed: none.
- Exported model/literal changes: `LparCreation` gained the
  `stamp_policy: Literal["best-effort", "required"]` field (defaults to `"best-effort"`), which
  moves the frozen public signature digest. The audit event vocabulary gained the
  `"tls-verification-disabled"` literal on `hmc_mcp.audit.Event`; that module is not part of the
  `hmc_mcp.api` facade, so it does not expand the manifest itself but is recorded here because it
  widens a public literal vocabulary.
- Unchanged otherwise: #410 rebuilt `hmc_install_lpar_os` / `hmc_install_vios`
  on the HMC CLI `installios` bridge (ADR 0070). These are MCP tools, not
  `hmc_mcp.api` exports; their parameter changes do not move the frozen
  manifest or its signature digest.

## [0.1.0] - 2026-08-22

Initial supported Python API surface per ADR 0029: the reusable facade at `hmc_mcp.api`, its
frozen export set (`tests/unit/test_public_api.py::test_public_api_manifest_is_frozen`) and its
frozen signature digest
(`tests/unit/test_public_api.py::test_public_operations_are_async_and_signatures_are_frozen`).

### Added

- MCP server and CLI for the IBM HMC REST API, the `hmc_mcp.api` supported facade, and the
  ownership-stamp workflow operations.

### Facade manifest

Initial manifest of `hmc_mcp.api.__all__` (127 exports; `set_lpar_ownership_description` above is
the only addition since):

`AdapterResult`, `AdapterType`, `AssignmentResult`, `AssignmentStep`, `AttachDiskResult`,
`BootDeviceSelector`, `ConfigError`, `DecommissionResult`, `DedicatedPcieAssignment`,
`DedicatedSlot`, `DeviceType`, `FleetHealthResult`, `HMCCLIError`, `HMCClient`, `HMCConfig`,
`HMCError`, `HMCTransportError`, `InventoryResult`, `InventorySelector`, `LparCreation`,
`LparCreationResult`, `LparPcieAssignments`, `LparPcieWorkflowResult`, `LparPowerResult`,
`LparResources`, `LpmResult`, `LuType`, `MetricKind`, `PartitionType`,
`PcieAssignmentUnavailableError`, `PcmCategory`, `ProvisionNetwork`, `ProvisionResult`,
`ProvisionStorage`, `SriovAdapter`, `SriovLogicalPort`, `SriovLogicalPortAssignment`,
`SriovLogicalPortCapabilityError`, `SriovLogicalPortChangeResult`,
`SriovLogicalPortPartialError`, `SriovLogicalPortSnapshot`, `SriovMode`, `SriovPhysicalPort`,
`StorageKind`, `VnicAssignment`, `VnicBackingSelector`, `VnicBackingSnapshot`,
`VnicCapabilityError`, `VnicChangeResult`, `VnicPartialError`, `VnicSnapshot`,
`abort_lpar_migration`, `add_network_adapter`, `add_vios_adapter`, `add_vnic`,
`apply_lpar_pcie_assignments`, `assign_dedicated_pcie_slot`, `assign_sriov_logical_port`,
`attach_disk_to_lpar`, `authorize_decommission_lpar_ownership_snapshot`,
`authorize_lpar_mutation`, `capacity_report`, `clear_lpar_boot_order`, `create_and_stamp_lpar`,
`create_logical_unit`, `create_media_repository`, `create_optical_media`,
`create_virtual_disk`, `create_virtual_network`, `create_volume_group`, `decommission_lpar`,
`delete_adapter`, `delete_logical_unit`, `delete_lpar`, `delete_media_repository`,
`delete_optical_media`, `delete_virtual_disk`, `delete_virtual_network`,
`deploy_partition_template`, `detach_storage_mapping`, `find_placement`, `fleet_health`,
`get_media_repository`, `get_partition_template`, `get_pcm_preferences`, `list_adapters`,
`list_dedicated_slots`, `list_fc_ports`, `list_network_bridges`, `list_optical_media`,
`list_partition_templates`, `list_sea_adapters`, `list_sriov_adapters`,
`list_sriov_logical_ports`, `list_sriov_physical_ports`, `list_storage_mappings`,
`list_virtual_networks`, `list_virtual_switches`, `list_vnics`, `list_volume_groups`,
`load_profile`, `lpar_summary`, `map_storage`, `metric_data`, `metric_links`, `migrate_lpar`,
`power_lpar`, `power_system`, `power_vios`, `prevalidate_lpar_pcie_assignments`,
`provision_lpar`, `read_lpar_boot_order`, `recover_lpar_migration`, `remote_restart_lpar`,
`remove_vnic`, `rename_lpar`, `resolve_lpar_ownership_names`, `resolve_pcm_resource`,
`set_lpar_boot_order`, `set_pcm_preferences`, `set_sriov_adapter_mode`,
`stamp_created_lpar_ownership`, `system_summary`, `unassign_dedicated_pcie_slot`,
`unassign_sriov_logical_port`, `upload_iso`

[unreleased]: https://github.com/randomparity/hmc-mcp/compare/0.1.0...HEAD
[0.1.0]: https://github.com/randomparity/hmc-mcp/releases/tag/v0.1.0
