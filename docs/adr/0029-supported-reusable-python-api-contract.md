# ADR 0029: Supported reusable Python API contract

## Status

Accepted (2026-08-15)

## Context

Reusable asynchronous workflows already live in presentation-neutral `operations_*.py` modules,
but consumers must import those implementation modules directly. The package has no declared
compatibility boundary for imports, call signatures, owned models, or the concrete client's many
inherited methods. Adding a facade without a complete selection rule would turn arbitrary current
layout choices into accidental promises.

ADR 0013 assigns shared workflows to operation modules and presentation concerns to MCP and CLI
adapters. Epic #186 requires one curated facade that follows that ownership boundary, retains
open-ended IBM HMC resource mappings, and defines strict pre-1.0 compatibility.

## Decision

`hmc_mcp.api` is the only supported reusable-library import path. Its explicit `__all__` is an
exhaustive compatibility manifest. An exported name, its call signature, and the fields and
constructor of an exported package-owned model are supported. Import paths outside
`hmc_mcp.api` are implementation details even when Python can import them.

During `0.x`, removing or renaming an export, making a compatible call invalid, or changing an
owned model incompatibly requires a minor release. Patch releases preserve the facade contract.
Additive compatible exports may ship in a patch release, but adding one is an intentional contract
change that must update the exact-export test. The `1.0.0` release may strengthen the policy but
must not silently weaken promises already made within its release line.

The facade exports `HMCClient`, `HMCConfig`, `ConfigError`, and `load_profile` for construction and
configuration. It exports the complete failure hierarchy callers need at the facade boundary:
`HMCError`, `HMCTransportError`, and `HMCCLIError`.

`HMCClient` is concrete so consumers can construct it, use it as an async context manager, and
inject a fake or alternate compatible object into operation functions. Its supported lifecycle
member allowlist is exactly `__init__`, `__aenter__`, `__aexit__`, `is_logged_on`, `logon`, and
`logoff`. Tests compare this allowlist with the declared contract. Inherited mixin methods and
generic UOM helpers remain callable and discoverable attributes, but are unsupported and may
change without a compatibility release. `__all__` cannot and does not hide those attributes.

Every current `operations_*.py` module is governed by one deterministic rule: export every
non-underscore top-level function and each package-owned input, result, enum, or literal-alias type
appearing in a selected function's public signature. Imported transport types such as `Any` and
built-in containers are not facade exports. The initial inventory is:

- `operations_adapters`: operations `list_adapters`, `add_network_adapter`,
  `add_vios_adapter`, and `delete_adapter`; types `AdapterResult` and `AdapterType`; no
  public-name exclusions.
- `operations_capacity`: operations `lpar_processing_units`, `system_capacity`,
  `capacity_report`, and `find_placement`; no owned types or public-name exclusions.
- `operations_composite`: operations `lpar_summary` and `system_summary`; no owned types;
  underscore helpers are internal.
- `operations_decommission`: operation `decommission_lpar`; type `DecommissionResult`;
  underscore helpers and `_Inventory` are internal.
- `operations_health`: operation `fleet_health`; type `FleetHealthResult`; underscore helpers are
  internal.
- `operations_lpar`: operations `power_on_outcome`, `parse_lpar_ownership_owner`,
  `authorize_decommission_lpar_ownership_snapshot`, `authorize_lpar_mutation`,
  `resolve_lpar_ownership_names`, `stamp_created_lpar_ownership`, `create_and_stamp_lpar`,
  `delete_lpar`, `power_lpar`, and `rename_lpar`; types `LparCreation`, `LparCreationResult`,
  `LparPowerResult`, and `LparPowerOnOutcome`; underscore helpers are internal.
- `operations_lpm`: operations `migrate_lpar`, `abort_lpar_migration`,
  `recover_lpar_migration`, and `remote_restart_lpar`; type `LpmResult`; underscore helpers are
  internal.
- `operations_network`: operations `list_virtual_switches`, `list_virtual_networks`,
  `create_virtual_network`, `delete_virtual_network`, and `list_network_bridges`; no owned types
  or public-name exclusions.
- `operations_pcm`: operations `resolve_pcm_resource`, `preference_flags`,
  `get_pcm_preferences`, `set_pcm_preferences`, `metric_links`, and `metric_data`; types
  `PcmCategory` and `MetricKind`; no public-name exclusions.
- `operations_provision`: operations `attach_disk_to_lpar` and `provision_lpar`; types
  `ProvisionNetwork`, `ProvisionStorage`, `ProvisionResult`, `AttachDiskResult`, `LparResources`,
  and `PartitionType`; underscore helpers are internal.
- `operations_ssh_network`: operations `list_fc_ports`, `list_sea_adapters`,
  `set_sriov_adapter_mode`, `list_vnics`, `add_vnic`, and `remove_vnic`; type `SriovMode`; no
  public-name exclusions.
- `operations_storage`: operations `list_volume_groups`, `create_volume_group`,
  `create_virtual_disk`, `map_storage`, `create_media_repository`, `create_optical_media`,
  `delete_media_repository`, `create_logical_unit`, `delete_logical_unit`,
  `validate_logical_unit_create`, and `validate_logical_unit_wait`; types `StorageKind`, `LuType`,
  and `DeviceType`; no public-name exclusions.
- `operations_systems`: operation `power_system`; no owned types or public-name exclusions.
- `operations_templates`: operations `list_partition_templates`, `get_partition_template`, and
  `deploy_partition_template`; no owned types; underscore helpers are internal.
- `operations_vios`: operation `power_vios`; no owned types or public-name exclusions.

Return annotations such as `dict[str, Any]`, `list[dict[str, Any]]`, and tuples containing those
values describe opaque HMC resource payload mappings. Their keys, nesting, and firmware-dependent
extensions are not package-owned model contracts. Exported dataclasses and literal aliases are
package-owned contracts. This distinction avoids promising that hmc-mcp controls IBM's open-ended
resource schema.

Generic UOM helpers, client mixin methods, XML/document builders, parser helpers, SSH command
primitives, MCP tools, CLI commands, server and CLI composition modules, and every underscore name
remain outside the supported contract. Public operations remain asynchronous where currently
declared and presentation-neutral; the facade must not import Typer, Rich, FastMCP, or MCP.

Presentation packages move together behind one `app` optional extra. Bare installation supports
the facade; `hmc-mcp[app]` supports both CLI and MCP entry paths. The retained-wheel library
contract runs on Python 3.13, while ADR 0020 continues to govern the package's broader CPython
support policy.

## Consequences

Consumers receive one explicit, testable import surface instead of depending on internal module
layout. Contract tests must freeze the exact `__all__`, lifecycle allowlist, async signatures,
presentation-import isolation, and absence of presentation types. Future operation modules and
public top-level functions do not enter the facade automatically: maintainers must consciously
update the facade, inventory, and tests.

The initial surface is broad because the deterministic rule includes policy and validation
functions as well as remote workflows. That breadth is preferable to an undocumented subjective
filter, but each included name now carries a compatibility cost. Opaque mapping results remain
flexible across HMC firmware, so consumers must tolerate additional or missing resource keys.

This ADR defines the contract only. Issues #189 through #192 own dependency extras, the facade and
contract tests, installed-wheel proof, and user documentation respectively.

## Considered & rejected

**Keep implementation-module imports as the supported API.** This exposes file ownership and makes
ordinary internal moves breaking changes.

**Re-export the facade from the package root.** The root already owns version discovery and CLI
startup. Adding a second supported import path creates duplicate contract surfaces without helping
library consumers.

**Select only operations currently needed by Bunson.** A consumer-specific list is subjective and
would omit equally presentation-neutral operations without a durable reason. The module ownership
rule is deterministic and applies to future review.

**Export every `HMCClient` method.** The concrete class composes transport and domain mixins, so
this would promise low-level UOM and implementation methods that the facade is meant to exclude.
A wrapper or duplicate protocol would add another implementation layer; a tested lifecycle
allowlist keeps the concrete client usable without blessing its full method set.

**Replace opaque mappings with new models.** hmc-mcp does not own IBM's evolving resource schemas.
Retyping them solely for facade uniformity would create false stability and substantial new scope.

**Split presentation dependencies across several extras.** The CLI and MCP are one installed app
surface and share presentation concerns. Multiple extras add unsupported installation combinations
without a current consumer need.
