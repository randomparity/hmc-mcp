# System-scoped name resolution design

Issue: #140
Decision: [ADR 0015](../../adr/0015-system-scoped-name-resolution.md)

## Goal and scope

Prevent global HMC searches from silently selecting the first of several
managed systems, LPARs, or VIOSes with the same name. Add optional managed-system
scope to destructive LPAR and VIOS tools so duplicate partition names remain
actionable. Broader selector plumbing, jobs, storage, and provisioning behavior
are excluded.

## Design

The three client finders share a fail-closed result-selection rule: zero results
returns `None`, one returns the entry, and more than one raises `ValueError` with
the requested name and every candidate UUID. LPAR and VIOS ambiguity messages
also show the parent managed-system name and UUID. Parent association is derived
only on the duplicate path by listing managed systems and their corresponding
child collection. The finder emits an ambiguity error only after every candidate
maps to exactly one named parent. Zero or multiple parent matches, or a failed
parent-discovery request, propagate as an actionable lookup failure; the finder
never emits a partial ambiguity diagnosis or returns result zero.

`find_partition_by_name(name, system_uuid=None)` and
`find_vios_by_name(name, system_uuid=None)` use the existing global search when
unscoped. When scoped they list the selected managed system's children and
filter `Resource.PartitionName` exactly. This avoids depending on unsupported
compound HMC search syntax.

`resolve_lpar_uuid` and `resolve_vios_uuid` gain keyword-only
`system_name_or_uuid=None`. For a name selector plus scope, they resolve the
system first and pass its UUID to the finder. A resource UUID remains a direct
pass-through and causes no system lookup; supplied system scope does not validate
the parent or authorize the UUID. Existing no-match messages remain byte-for-byte
unchanged.

The destructive tool surface gains optional system scope wherever a destructive
LPAR/VIOS operation resolves a name: LPAR delete and rename use their existing
required system selectors for resolution; LPAR power-off gains an optional selector;
VIOS delete, restore, and power-off gain optional selectors. Presentation-neutral
operations and the SSH restore helper receive the resolved scope without
changing unrelated read or provisioning tools.

## Error handling

Ambiguity is a caller-correctable `ValueError`, matching existing resolver
failures. Candidate diagnostics are deterministically sorted by managed-system
name, managed-system UUID, and resource UUID. Missing parent metadata or a
parent-discovery failure aborts resolution before any mutation and never
restores first-result behavior.

## Security model

The trust boundary is an authenticated MCP caller supplying a name and optional
system selector that can cause an HMC mutation. Existing ownership and state
checks remain authoritative after resolution. The new control is exact scoped
matching plus fail-closed ambiguity detection before mutation. Diagnostics leak
only inventory names and UUIDs already available through list tools to the same
authenticated caller. Authorization policy and ownership override behavior are
out of scope and unchanged.

Unscoped parent discovery is bounded to 100 managed systems so one caller
request cannot amplify into an arbitrary number of child-inventory requests.
The aggregate child-inventory scan has a 30-second deadline. Larger inventories
and timed-out scans fail with guidance to supply system scope.

## Tests

Client tests cover duplicate managed systems and duplicate LPAR/VIOS names on
two systems, including complete candidate diagnostics. Resolver tests cover
scoped forwarding, UUID pass-through, and unchanged no-match guidance. Tool and
operation tests prove each destructive name path forwards scope before any
mutation. Client tests also trigger a missing parent, a candidate associated
with multiple parents, and a failed parent-discovery request; each must propagate
an actionable lookup failure without returning a candidate or attempting a
mutation. Malformed or repeated candidate UUIDs fail closed, and inventories
over the parent-discovery budget direct the caller to provide system scope.
mutation. Existing single-result tests remain as regression proof. The final
gate is `just verify`.

## Durable workflow context

- Branch: `feat/ambiguous-resolver-scope-140`
- Base branch: `main`
- Guardrail: `just verify`
- Host architecture: `arm64`
- Target architectures: none declared
- Architecture relationship: `no-target-declared`
