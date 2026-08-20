# Normalized PCIe inventory design

## Scope

Issue #212 adds system-scoped, presentation-neutral collection contracts for dedicated PCIe slots
and the three SR-IOV levels defined by [ADR 0053](../../adr/0053-evidence-backed-pcie-capability-contract.md).
It does not add or alter mutation behavior. The implementation is governed by
[ADR 0054](../../adr/0054-normalized-pcie-inventory.md).

## Contract

Four distinct operations expose dedicated slots, SR-IOV adapters, physical ports, and logical
ports. Each returns an `InventoryResult[T]` with `resource_kind`, `capability`, `items`, and
`unavailable_reason`. `capability` is `available` only when an evidence-admitted read command has
succeeded and its exact projection parsed; `items` may then be empty. `capability-unavailable`
always carries an empty list and a stable reason. Command failure and malformed successful output
raise an error and never become either state.

`DedicatedSlot` has the required identity components `system` and `drc_index`, plus `description`
and `owner_lpar`. An empty owner is normalized to `None`; no field is inferred. The dedicated
collector uses exactly the ADR 0053 Power9 projection
`drc_index,description,lpar_name --header`. A blank `drc_index` is a contract error.

`SriovAdapter`, `SriovPhysicalPort`, and `SriovLogicalPort` define their stable hierarchy and
selectors, but every attribute whose read projection ADR 0053 does not admit is typed as unknown.
Their collection operations therefore return `capability-unavailable` without issuing an HMC
command. The stable reasons name the missing evidence, and the schema documents that future
evidence may make the same result type available. Capacity fields are `Decimal | None` percentages;
they are never byte counts, bandwidth, or integer weights. Physical and logical-port records retain
the parent selector components listed below.

## Schema

Closed literals are:

- `CapabilityState = Literal["available", "capability-unavailable"]`;
- `ResourceKind = Literal["dedicated_slot", "sriov_adapter", "sriov_physical_port",
  "sriov_logical_port"]`;
- the unavailable reason is exactly
  `ADR 0053 admits selectors but no SR-IOV read projection`.

Every public model is an immutable dataclass. `InventorySelector` contains
`adapter_id: str | None = None`, `physical_port_id: str | None = None`, and
`logical_port_id: str | None = None`. `InventoryResult[T]` contains, in order,
`resource_kind: ResourceKind`, `capability: CapabilityState`, `system: str`,
`selector: InventorySelector`, `items: list[T]`, and `unavailable_reason: str | None`.
An available result has `unavailable_reason is None`; an unavailable result has `items == []` and
the exact reason above. The selector records only caller-supplied narrowing, not inferred resource
existence.

| Item | Exact fields in order |
|---|---|
| `DedicatedSlot` | `system: str`, `drc_index: str`, `description: str | None`, `owner_lpar: str | None` |
| `SriovAdapter` | `system: str`, `adapter_id: str`, `mode: str | None`, `location_code: str | None`, `owner_lpar: str | None`, `logical_ports_in_use: int | None`, `logical_ports_available: int | None` |
| `SriovPhysicalPort` | `system: str`, `adapter_id: str`, `physical_port_id: str`, `location_code: str | None`, `owner_lpar: str | None`, `minimum_capacity_granularity_percent: Decimal | None`, `logical_ports_in_use: int | None`, `logical_ports_available: int | None` |
| `SriovLogicalPort` | `system: str`, `adapter_id: str`, `physical_port_id: str | None`, `logical_port_id: str`, `owner_lpar: str | None`, `owner_lpar_id: str | None`, `capacity_percent: Decimal | None`, `maximum_capacity_percent: Decimal | None`, `compatibility: str | None` |

Issue #212 explicitly requires mode, availability, ownership, location, capacity, compatibility,
and unknown-field categories in the stable result. The corresponding optional fields above name
those requested categories; they do not claim an HMC read projection or a closed value vocabulary.
ADR 0053 admits no rows from which to populate them today, so every one remains `None`. Populating
one requires a version-labelled fixture and a reviewed schema revision. Stable identity is system
plus `adapter_id` for an adapter, adapter
identity plus `physical_port_id` for a physical port, and adapter identity plus `logical_port_id`
for a logical port; `physical_port_id` on the logical record preserves hierarchy but is not part of
that identity.

Operation signatures are:

- `list_dedicated_slots(config, system)`;
- `list_sriov_adapters(config, system, adapter_id=None)`;
- `list_sriov_physical_ports(config, system, adapter_id=None, physical_port_id=None)`; and
- `list_sriov_logical_ports(config, system, adapter_id=None, physical_port_id=None,
  logical_port_id=None)`.

Each optional selector is copied verbatim to `InventorySelector`. It narrows caller intent only;
while capability is unavailable it never creates an item or claims that identity exists.

The reusable Python API exports the result and item types and the four operations. MCP exposes four
read tools under the managed-system target. CLI exposes four JSON-capable network inventory
commands; unavailable results render as the result object rather than the misleading empty-list
message. The legacy raw `hmc_list_io_slots` entry point remains for compatibility and is not used
as the normalized contract.

## Data flow and errors

The SSH boundary builds the fixed dedicated-slot command and parses it with
`parse_hmc_delimited_rows`. A presentation-neutral module resolves a system selector, calls that
boundary, and constructs immutable records. MCP converts records with `dataclasses.asdict`; CLI
does the same before JSON/table output. Selectors are never concatenated into a command without
the existing shell quoting. SR-IOV operations resolve the system selector so returned unavailable
results remain system-scoped, but perform no inventory command.

Malformed headers, missing identity, extra/missing columns, and non-successful HMC commands remain
errors. Successful header-only output is an available empty collection. Empty optional fields are
explicit `None`. Unknown SR-IOV fields are explicit `None`; unavailable collections are explicit
at the collection level.

## Testing

Focused tests first prove the fixed command, exact header, header-only result, missing optional
fields, missing identity rejection, and lossless owner/identity normalization. Model tests pin
every row in the `Schema` table, hierarchy selectors, `Decimal` percentage capacity, explicit
unknowns, and serialization. Contract
tests pin reusable API exports, MCP signatures/security metadata, CLI commands/JSON output, and the
fact that unavailable SR-IOV collectors issue no HMC inventory command. `just verify` is the final
gate.

## Threat model

The change widens existing authenticated MCP/CLI read surfaces and reuses two existing boundaries:
operator-controlled system selectors enter SSH name resolution, and HMC-produced CSV enters the
strict parser. Existing target authorization protects MCP dispatch; existing name resolution and
`shlex.quote` protect command construction; exact headers, column counts, and nonblank identities
protect parsing. Errors expose operation and contract context but no credentials. Anonymous actors,
new authorization models, mutation, and unsupported-error classification are out of scope because
this design neither creates those paths nor has evidence to define them.

## Global constraints

- Python 3.13, `uv`, immutable dataclasses, absolute imports, and 100-character lines.
- No new dependency, inferred HMC field, unsupported-error classifier, mutation, or ADR index edit.
- Stable identities and percentage units are exactly those accepted by ADR 0053.
- Guardrail: `just verify`.
