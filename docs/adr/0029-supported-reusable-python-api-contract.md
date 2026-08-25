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
exhaustive compatibility manifest. An exported name, its call signature, the fields and
constructor of an exported package-owned model, and the members and values of an exported enum or
literal alias are supported. Import paths outside `hmc_mcp.api` are implementation details even
when Python can import them.

During `0.x`, removing or renaming an export, making a compatible call invalid, or changing an
owned model incompatibly requires a minor release. Adding, removing, renaming, or changing an
exported enum member or literal alternative also requires a minor release. Additive facade exports
require a minor release because they expand the manifest. Patch releases are limited to compatible
fixes that change neither the export set nor enum and literal value sets. The `1.0.0` release may
strengthen the policy but must not silently weaken promises already made within its release line.

The facade exports `HMCClient`, `HMCConfig`, `ConfigError`, and `load_profile` for construction and
configuration. It exports the complete failure hierarchy callers need at the facade boundary:
`HMCError`, `HMCTransportError`, and `HMCCLIError`.

`HMCClient` is concrete so consumers can construct it, use it as an async context manager, and
inject a constructed instance into operation functions. Its supported lifecycle member allowlist
is exactly `__init__`, `__aenter__`, `__aexit__`, `is_logged_on`, `logon`, and `logoff`. Tests
compare this allowlist with the declared contract. Inherited mixin methods and generic UOM helpers
remain callable and discoverable attributes, but are unsupported and may change without a
compatibility release. `__all__` cannot and does not hide those attributes. Duck-typed fakes may
exercise operations in tests, but this ADR does not define or promise a public alternate-client
protocol.

Every current `operations_*.py` module is governed by one deterministic rule: export every
non-underscore top-level coroutine function the module itself defines, and each package-owned
input, result, enum, or literal-alias type appearing in a selected function's public signature.
An asynchronous helper that a module imports from elsewhere is a top-level name in that module's
namespace but is owned by the module that defines it, so ownership decides selection and no name
is selected twice; the per-module exclusion notes below concern only names a module defines. A
synchronous function is a transformation, parser, or validator rather than an asynchronous domain
operation and is excluded for that concrete contract-readiness reason. Imported transport types
such as `Any` and built-in containers are not facade exports. The initial inventory is:

- `operations_adapters`: operations `list_adapters`, `add_network_adapter`,
  `add_vios_adapter`, and `delete_adapter`; types `AdapterResult` and `AdapterType`; no
  public-name exclusions.
- `operations_capacity`: operations `capacity_report` and `find_placement`; no owned types;
  synchronous calculation helpers `lpar_processing_units` and `system_capacity` are excluded.
- `operations_composite`: operations `lpar_summary` and `system_summary`; no owned types;
  underscore helpers are internal.
- `operations_decommission`: operation `decommission_lpar`; type `DecommissionResult`;
  underscore helpers and `_Inventory` are internal.
- `operations_health`: operation `fleet_health`; type `FleetHealthResult`; underscore helpers are
  internal.
- `operations_install`: operations `install_lpar_os` and `install_vios`; no owned types or
  public-name exclusions. Both submit the detached `installios` CLI bridge ADR 0070 selected
  after ADR 0069 found no `InstallLPAR` or `InstallVIOS` REST job on any surveyed HMC, so each
  returns the bridge's detach handle — an opaque `dict[str, Any]` carrying the resolved system
  and partition names, the remote PID, the install log path, and a restating message — rather
  than an HMC job identifier. Nothing on this path is pollable, so no wait parameters are
  offered and none may be added without a superseding decision. Both carry the ADR 0092 §6
  classification recorded in their docstrings: Destructive under §2, outside §1's ownership
  guard by resource type, because `installios` requires a Virtual I/O Server partition and
  ADR 0011 stamps no ownership token on one.
- `operations_lpar`: operations `assess_post_activation_affinity`,
  `authorize_decommission_lpar_ownership_snapshot`,
  `authorize_lpar_mutation`, `resolve_lpar_ownership_names`, `stamp_created_lpar_ownership`,
  `create_and_stamp_lpar`, `delete_lpar`, `power_lpar`, `rename_lpar`, and
  `set_lpar_ownership_description`; types `LparCreation`,
  `LparCreationResult`, and `LparPowerResult`; synchronous result helper `power_on_outcome` and
  ownership parser `parse_lpar_ownership_owner` are excluded; underscore helpers are internal.
- `operations_lpm`: operations `migrate_lpar`, `abort_lpar_migration`,
  `recover_lpar_migration`, and `remote_restart_lpar`; type `LpmResult`; underscore helpers are
  internal.
- `operations_network`: operations `list_virtual_switches`, `list_virtual_networks`,
  `create_virtual_network`, `delete_virtual_network`, and `list_network_bridges`; no owned types
  or public-name exclusions.
- `operations_pcm`: operations `resolve_pcm_resource`, `get_pcm_preferences`,
  `set_pcm_preferences`, `metric_links`, and `metric_data`; types `PcmCategory` and `MetricKind`;
  synchronous flag builder `preference_flags` is excluded.
- `operations_provision`: operations `attach_disk_to_lpar` and `provision_lpar`; types
  `ProvisionNetwork`, `ProvisionStorage`, `ProvisionResult`, `AttachDiskResult`, `LparResources`,
  and `PartitionType`; underscore helpers are internal.
- `operations_ssh_network`: operations `list_fc_ports`, `list_sea_adapters`,
  `get_lpar_memopt_score`, `list_lpar_memopt_scores`, `set_sriov_adapter_mode`,
  `get_system_memopt_score`, `plan_lpar_memopt_scores`, `plan_system_memopt_score`,
  `list_vnics`, `add_vnic`, and `remove_vnic`; types `MemoptLparSelector` and `SriovMode`; no public-name
  exclusions.
- `operations_storage`: operations `list_volume_groups`, `create_volume_group`,
  `create_virtual_disk`, `delete_virtual_disk`, `map_storage`, `list_storage_mappings`,
  `detach_storage_mapping`, `upload_iso`, `create_media_repository`, `get_media_repository`,
  `delete_media_repository`, `create_optical_media`, `list_optical_media`,
  `delete_optical_media`, `list_optical_mappings`, `mount_optical_media`,
  `unmount_optical_media`, `create_logical_unit`, and `delete_logical_unit`; types `StorageKind`,
  `LuType`, and `DeviceType`; synchronous validators `validate_logical_unit_create` and
  `validate_logical_unit_wait` are excluded.
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
remain outside the supported contract. Every public operation is asynchronous and
presentation-neutral; the facade must not import Typer, Rich, FastMCP, or MCP.

Presentation packages move together behind one `app` optional extra. Bare installation supports
the facade; `hmc-mcp[app]` supports both CLI and MCP entry paths. The retained-wheel library
contract runs on Python 3.13, while ADR 0020 continues to govern the package's broader CPython
support policy.

## Consequences

Consumers receive one explicit, testable import surface instead of depending on internal module
layout. Contract tests must freeze the exact `__all__`, lifecycle allowlist, asynchronous
signatures, enum and literal value sets, presentation-import isolation, and absence of presentation
types. Future operation modules and public top-level functions do not enter the facade
automatically: maintainers must consciously update the facade, inventory, and tests. A contract
test applies the *operation* half of the selection rule above to every `operations_*` module by
introspection and fails when a selected coroutine function is missing from `__all__` without a
recorded justification citing this ADR, so operation-side drift of the kind the optical-media
operations went through cannot recur silently. Two parts of this section remain hand-maintained
and untested: the type half of the rule, and the per-module inventory prose above. Neither is
compared against the code by any test, so both can be wrong while the suite is green.

The initial surface is broad because the deterministic rule includes every asynchronous domain
operation, including policy-enforcement workflows. That breadth is preferable to an undocumented
subjective filter, but each included name now carries a compatibility cost. Synchronous
transformation, parsing, and validation helpers remain internal. Opaque mapping results remain
flexible across HMC firmware, so consumers must tolerate additional or missing resource keys.

This ADR defines the contract only. Issues #189 through #192 own dependency extras, the facade and
contract tests, installed-wheel proof, and user documentation respectively.

## Considered & rejected

**Continue without a supported reusable import boundary.** This preserves maximum internal
freedom but leaves Bunson and other in-process consumers coupled to implementation paths and gives
them no release signal for compatibility.

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

**Export synchronous helpers alongside asynchronous operations.** That would make parsers,
transformations, and validators part of the facade even though the reusable operation contract
requires public calls to remain asynchronous. Keeping them internal also avoids promising helper
functions whose current module visibility was not designed as a consumer boundary.

**Replace opaque mappings with new models.** hmc-mcp does not own IBM's evolving resource schemas.
Retyping them solely for facade uniformity would create false stability and substantial new scope.

**Split presentation dependencies across several extras.** The CLI and MCP are one installed app
surface and share presentation concerns. Multiple extras add unsupported installation combinations
without a current consumer need.
