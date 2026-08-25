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
is selected twice; a module's `excluded synchronous` clause below names only what it defines.
Selection is keyed by `(module, name)`, not by bare name: two modules defining the same public
name are two separate obligations, and exporting one does not discharge the other. Because
`hmc_mcp.api` can bind only one object per name, a real collision cannot be satisfied by
exporting both — rename one of the operations, or record an exclusion citing this ADR. A
synchronous function is a transformation, parser, or validator rather than an asynchronous domain
operation and is excluded for that concrete contract-readiness reason. Imported transport types
such as `Any` and built-in containers are not facade exports.

That type half is transitive through an exported model's fields. This Decision already calls the
fields of an exported package-owned model supported, and a supported field is a promise a consumer
cannot use unless they can name the field's type: they cannot annotate a variable holding one,
write a helper that takes one, or narrow a union containing one. So selection follows a selected
model's own fields — a dataclass's `dataclasses.fields`, a Pydantic model's `model_fields`, and a
`TypedDict`'s keys — to a fixed point, and every package-owned type or literal alias reached that
way is itself selected. The closure is seeded from `__all__` as well as from operation signatures,
because an exported model need not appear in any selected operation's signature. A field annotated
as an opaque HMC payload mapping owns no `hmc_mcp` type and adds nothing, and an underscore name is
internal here as everywhere.

Only two exported class kinds are none of those three shapes and so expose no field this clause can
read: `HMCClient`, whose supported surface is the lifecycle allowlist above, and the exported error
types, whose supported surface is their constructor. A contract test holds the facade to exactly
that pair, so a result type introduced in a fourth shape — a `NamedTuple`, an `attrs` class, a
hand-written one — fails the suite instead of dropping out of this clause unnoticed.

For those two kinds the type half reads `__init__` in place of the fields. Their constructor
parameter types *are* selected: this Decision's first paragraph already calls an exported name's
call signature supported, and a class's call signature is its constructor, so a consumer who
catches an exported error or constructs an exported client must be able to name what the
constructor takes and what it exposes. Every package-owned type or literal alias a selected
constructor's parameters name is therefore selected too, on the same terms as an operation's
parameters and by the same transitive closure — a model reached through a constructor is walked
for its own fields in turn. For a model this reads nothing new, because a model's constructor
parameters are its fields, so the clause is stated for the classes whose fields cannot be read.
A constructor inherited from outside the package — `RuntimeError.__init__`, `ValueError.__init__` —
carries no annotation and names nothing.

The rule reads a module attribute exactly as `inspect.iscoroutinefunction` and `__module__`
ownership report it, so three operation shapes fall outside it by decision rather than by
oversight: an asynchronous generator, which satisfies `inspect.isasyncgenfunction` and not
`iscoroutinefunction`; an operation built by a factory that lives in another module, whose
`__module__` names the factory's module; and an asynchronous `functools.partial`, whose
`__module__` is `functools`. None exists in the package today, none may be introduced without a
superseding decision that widens this rule, and a contract test fails when one of those three
appears, so the choice is a conscious one rather than an invisible omission. A *synchronous*
partial is an ordinary transformation helper and is covered by the synchronous exclusion above.
`functools.wraps` is unaffected — it copies `__module__`, so an ordinary decorator preserves
ownership.

The inventory below is the complete manifest: one entry per module `hmc_mcp/api.py` imports a
supported name from, keyed by that import source. Each `operations_*` entry names the operations
the rule selects from that module, the other supported names the facade takes from it, and the
public synchronous functions the module defines and this contract keeps internal. Every other
entry names what the facade takes from a module that owns no operations. Contract tests assert
every clause against the facade's own import statements and the modules' contents, so the
document cannot drift from the package. Entries and the names within each clause are in
alphabetical order, because the tests compare them against sorted derivations. Narrative belongs
in an indented `Note:` sub-bullet, and the text indented under such a bullet is the one thing
between the fence markers the parser does not check — it is prose, and it is hand-maintained.
Every other line must be an entry or an entry's wrapped continuation: a bare paragraph, a
sub-bullet that is not a `Note:`, and a line that dedents back out of a note all fail the suite
rather than passing unchecked. A normative claim therefore may not be written as note narrative,
because nothing would hold it to the code. Underscore
names are internal everywhere and are never inventoried.

<!-- ADR-0029-INVENTORY:BEGIN -->

- `affinity_assessment` — exports: `AffinityAssessmentInput`, `AffinityAssessmentResult`,
  `AffinityClassification`, `AffinityEvidence`, `CapturedPolicyState`, `PolicyState`.
- `client` — exports: `HMCClient`.
- `client_adapters` — exports: `AdapterType`.
- `config` — exports: `ConfigError`, `HMCConfig`, `load_profile`.
  - Note: `load_profile` is synchronous and exported all the same. It is a configuration
    constructor, not a domain operation, and the synchronous-exclusion reason above does not
    reach it.
- `console_capture` — exports: `ConsoleCapture`, `ConsoleHeldError`, `StopReason`,
  `capture_lpar_console`.
  - Note: `capture_lpar_console` is an operation living outside `operations_*` (ADR 0072), so
    the selection rule does not reach it; it is exported by this entry alone.
- `documents` — exports: `BootDeviceSelector`, `Keylock`, `LparResources`, `OsType`,
  `PartitionType`, `SharingMode`, `StorageKind`.
- `errors` — exports: `HMCError`, `HMCTransportError`.
- `jobs` — exports: `DeviceType`, `JobOutcome`, `LuType`, `RemoteRestartOperation`.
  - Note: `JobOutcome`'s fields are a package-owned model contract except the opaque `job`
    mapping (ADR 0093). The synchronous helpers `job_identifier`, `job_outcome`, and
    `validate_wait_timing` stay in `jobs.py` as transformations and validators.
- `operations_adapters` — operations: `add_network_adapter`, `add_vios_adapter`, `delete_adapter`,
  `list_adapters`; types: `AdapterResult`; excluded synchronous: none.
- `operations_assignments` — operations: `apply_lpar_pcie_assignments`,
  `prevalidate_lpar_pcie_assignments`; types: `AssignmentResult`, `AssignmentStep`,
  `DedicatedPcieAssignment`, `LparPcieAssignments`, `LparPcieWorkflowResult`,
  `SriovLogicalPortAssignment`, `VnicAssignment`; excluded synchronous: none.
- `operations_capacity` — operations: `capacity_report`, `find_placement`; types: none; excluded
  synchronous: `lpar_processing_units`, `system_capacity`.
- `operations_composite` — operations: `lpar_summary`, `system_summary`; types: none; excluded
  synchronous: none.
- `operations_decommission` — operations: `decommission_lpar`; types: `DecommissionResult`;
  excluded synchronous: none.
- `operations_health` — operations: `fleet_health`; types: `FleetHealthResult`; excluded
  synchronous: none.
- `operations_install` — operations: `install_lpar_os`, `install_vios`; types: none; excluded
  synchronous: `validate_install_request`.
  - Note: the MCP tools call `validate_install_request` to reject a malformed argument before a
    client is opened, which the operations cannot do. Both operations submit the detached
    `installios` CLI bridge ADR 0070 selected after ADR 0069 found no `InstallLPAR` or
    `InstallVIOS` REST job on any surveyed HMC, so each returns the bridge's detach handle — a
    `dict[str, Any]` carrying the resolved system and partition names, the remote PID, the
    install log path, and a restating message — rather than an HMC job identifier. That mapping
    is **not** one of the opaque HMC resource payloads the Consequences section below describes:
    this package composes all five keys itself and no firmware level can vary them, so `system`,
    `partition`, `pid`, `log_path` and `message` are a package-owned contract, frozen by a test
    rather than by the signature digest, and changing one needs the same minor release an
    `__all__` change does. Recording that shape in the annotation so the digest can see it is
    tracked by #468. Nothing on this path is pollable, so no wait parameters are offered and none
    may be added without a superseding decision. Both are classified for ownership authorization
    in ADR 0092 §3.4a, which is the authoritative record; §6's recording obligation for a new
    facade export is discharged there, not here.
- `operations_jobs` — operations: `get_job`, `wait_for_job`; types: none; excluded synchronous:
  none.
- `operations_lpar` — operations: `assess_post_activation_affinity`,
  `authorize_decommission_lpar_ownership_snapshot`, `authorize_lpar_mutation`,
  `clear_lpar_boot_order`, `create_and_stamp_lpar`, `delete_lpar`, `list_lpar_ownership`,
  `power_lpar`, `read_lpar_boot_order`, `rename_lpar`, `resolve_lpar_ownership_names`,
  `set_lpar_boot_order`, `set_lpar_memory`, `set_lpar_ownership_description`,
  `set_lpar_processors`, `stamp_created_lpar_ownership`; types: `LparCreation`,
  `LparCreationResult`, `LparPowerResult`, `ProvisionAffinityAssessment`; excluded synchronous:
  `activation_allows_assessment`, `affinity_not_measured`, `classify_affinity_outcome`,
  `lpar_ownership_entry`, `parse_lpar_ownership_caller_token`, `parse_lpar_ownership_owner`,
  `power_on_outcome`, `validate_affinity_request`.
  - Note: `ProvisionAffinityAssessment` is defined here and used by `provision_lpar` as well.
    The inventory keys on the module `api.py` imports a name from, not on the module that
    defines it; those two agree here because the facade was changed to import it from this
    module. They do not always agree — `MemoptLparSelector` and `MemoptResourceGroupSelector`
    are defined in `ssh_commands` and inventoried under `operations_ssh_network`, which is where
    the facade takes them from.
- `operations_lpm` — operations: `abort_lpar_migration`, `migrate_lpar`,
  `migrate_lpar_with_affinity_preflight`, `recover_lpar_migration`, `remote_restart_lpar`,
  `run_lpm_affinity_preflight`; types: `LpmAffinityMigrationResult`,
  `LpmAffinityPreflightOutcome`, `LpmAffinityPreflightRequest`, `LpmResult`; excluded synchronous:
  `evaluate_lpm_affinity_preflight`.
- `operations_network` — operations: `create_virtual_network`, `delete_virtual_network`,
  `list_network_bridges`, `list_virtual_networks`, `list_virtual_switches`; types: none; excluded
  synchronous: none.
- `operations_pcie` — operations: `assign_dedicated_pcie_slot`, `assign_sriov_logical_port`,
  `list_dedicated_slots`, `list_sriov_adapters`, `list_sriov_logical_ports`,
  `list_sriov_physical_ports`, `set_sriov_adapter_mode`, `unassign_dedicated_pcie_slot`,
  `unassign_sriov_logical_port`; types: `CapabilityState`, `DedicatedSlot`, `InventoryResult`,
  `InventorySelector`, `PcieAssignmentUnavailableError`, `ResourceKind`, `SriovAdapter`,
  `SriovLogicalPort`, `SriovLogicalPortCapabilityError`, `SriovLogicalPortChangeResult`,
  `SriovLogicalPortPartialError`, `SriovLogicalPortSnapshot`, `SriovPhysicalPort`; excluded
  synchronous: none.
- `operations_pcm` — operations: `get_pcm_preferences`, `metric_data`, `metric_links`,
  `resolve_pcm_resource`, `set_pcm_preferences`; types: `MetricKind`, `PcmCategory`,
  `PcmResource`; excluded synchronous: `preference_flags`, `validate_pcm_metric_target`,
  `validate_pcm_preferences_category`.
- `operations_provision` — operations: `attach_disk_to_lpar`, `provision_lpar`; types:
  `AttachDiskResult`, `ProvisionNetwork`, `ProvisionResult`, `ProvisionStorage`; excluded
  synchronous: none.
- `operations_snapshot` — operations: `assess_snapshot_affinity`, `capture_lpar_snapshot`,
  `inspect_lpar_snapshot`, `validate_lpar_snapshot`; types: none; excluded synchronous: none.
- `operations_ssh_network` — operations: `add_vnic`, `get_lpar_memopt_score`,
  `get_minimum_affinity_policy`, `get_system_memopt_score`, `list_fc_ports`,
  `list_lpar_memopt_scores`, `list_resource_group_memopt_scores`, `list_sea_adapters`,
  `list_vnics`, `plan_lpar_memopt_scores`, `plan_resource_group_memopt_scores`,
  `plan_system_memopt_score`, `remove_vnic`, `set_minimum_affinity_policy`; types:
  `MemoptLparSelector`, `MemoptResourceGroupSelector`, `MinimumAffinityPolicyResult`,
  `ResourceGroupAffinityResult`, `VnicBackingSelector`, `VnicBackingSnapshot`,
  `VnicCapabilityError`, `VnicChangeResult`, `VnicPartialError`, `VnicSnapshot`; excluded
  synchronous: none.
- `operations_storage` — operations: `create_logical_unit`, `create_media_repository`,
  `create_optical_media`, `create_virtual_disk`, `create_volume_group`, `delete_logical_unit`,
  `delete_media_repository`, `delete_optical_media`, `delete_virtual_disk`,
  `detach_storage_mapping`, `get_media_repository`, `list_optical_mappings`, `list_optical_media`,
  `list_storage_mappings`, `list_volume_groups`, `map_storage`, `mount_optical_media`,
  `unmount_optical_media`, `upload_iso`; types: none; excluded synchronous:
  `validate_logical_unit_create`, `validate_logical_unit_wait`.
- `operations_systems` — operations: `power_system`; types: none; excluded synchronous: none.
- `operations_templates` — operations: `deploy_partition_template`, `get_partition_template`,
  `list_partition_templates`; types: none; excluded synchronous: none.
- `operations_vios` — operations: `power_vios`; types: none; excluded synchronous: none.
- `snapshot` — exports: `HmcIdentity`, `LparIdentity`, `LparSnapshot`, `MemoryProjection`,
  `NativeProfile`, `NormalizedConfiguration`, `ObservationEnvelope`, `ProcessorProjection`,
  `SnapshotCapability`, `SnapshotConfiguration`, `SnapshotInspection`, `SnapshotObservations`,
  `SnapshotSource`, `SnapshotValidationError`, `SystemIdentity`.
  - Note: every name here but the three that were already exported is a field type of
    `LparSnapshot`, selected by the Decision's transitive type clause rather than by appearing
    in an operation's signature. `SnapshotInspection` reaches none of them: its own fields are
    strings, booleans, and opaque mappings.
- `ssh` — exports: `HMCCLIError`.
- `ssh_commands` — exports: `MinimumAffinityPolicy`, `SriovMode`.

<!-- ADR-0029-INVENTORY:END -->

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
operations went through cannot recur silently.

The type half is mechanised the same way: a second test resolves each selected operation's
annotations with `typing.get_type_hints`, collects every `hmc_mcp`-owned type they name — through
containers, unions, and `Callable` parameter lists — and fails when one is not in `__all__` and
bound on `hmc_mcp.api` under that name, unless an exclusion cites this ADR. `get_type_hints`
evaluates a literal alias down to its value set and loses the alias's name, so that clause is read
from the annotation source text instead. Each `operations_*` module carries `from __future__
import annotations` — asserted, not assumed, because a module without it would drop out of this
clause — which leaves every annotation as source: a bare name, a dotted reference, and a quoted
forward reference are all resolved in the module that carries the operation, `Annotated` metadata
is unwrapped, and whatever turns out to be a literal alias is kept. Both halves key on the module
that *defines* a type, which for an alias is recovered by following the `from ... import`
statements that bound the name, so an alias arriving from outside the package is not a facade
export. `PcmResource` and `RemoteRestartOperation` were the two omissions the two halves found. An
opaque HMC payload mapping owns no `hmc_mcp` type and so is excluded by construction.

Both halves then run again over model fields, which is how the mechanism carries the Decision's
transitive clause. The walk closes over the `dataclasses.fields`, `model_fields`, or `TypedDict`
keys of every owned model it has reached and of every model `__all__` exports, and the source-text
alias clause is read off those same field annotations — through each model's MRO, because an
inherited field carries its annotation on the base that declares it. That closure found nineteen
further omissions (#482): twelve `snapshot` models behind `LparSnapshot`, and seven literal
aliases — `AffinityClassification`, `CapabilityState`, `Keylock`, `OsType`,
`ResourceKind`, `SharingMode`, and `StopReason` — that no selected signature named. One limit
remains deliberate: an underscore name is internal here as everywhere.

Both halves run a third time over the constructors of the exported classes the field walk reads no
field off — the pair the Decision names above. `typing.get_type_hints(cls.__init__)` feeds the type
half and the raw `__init__` annotations feed the alias half, resolved in the module that defines
the constructor rather than the module of the class that inherits it. Constructor types are
collected before the field closure runs, so a model a constructor names is walked for its own
fields too. This one found no omission (#502): `HMCClient` takes `HMCConfig`, and
`SriovLogicalPortPartialError` and `VnicPartialError` take `SriovLogicalPortChangeResult` and
`VnicChangeResult`, all four already exported. It is a guard against a future error type carrying
an unexported result, which a consumer would meet through a supported `except` clause with no
supported import path to name it — so a synthetic exported error whose constructor names an
unexported type and an unexported alias drives the clause, because the live facade exercises only
its clean path.

A third test parses the inventory above and asserts each clause against the facade's own import
statements and the modules' contents, rejecting every line inside the fence that is neither an
entry nor a `Note:`'s own narrative rather than skipping it; a fourth rejects a repeated entry in
`__all__`, which the set-based contract tests
were blind to and the frozen list had written into it; and a fifth fails when an `operations_*`
module gains an asynchronous generator, a factory-built operation, or an asynchronous
`functools.partial`. What remains hand-maintained is the narrative in each `Note:` sub-bullet,
the two exclusion mappings, the frozen `__all__` list, and the frozen signature digest; every
clause of the inventory itself is now compared against the code.

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
