# ADR 0084: Gate resource-group affinity at the HMC command boundary

## Status

Accepted

## Context

Resource-group affinity is a Power11 capability exposed by `lsmemopt` on HMC
V11R1M1110 and later. Version-labelled live evidence on issue #312 establishes two distinct
unsupported paths: HMC V10 rejects the `resgroup`, `-g`, and `--gid` grammar, while HMC V11
accepts that grammar and reports `HSCLCA00` when the managed system lacks multiple resource
groups. The same evidence establishes the current and calculated output schemas.

The supported Python API, MCP tools, and CLI need one presentation-neutral contract that never
offers score rows when the capability is unavailable and never turns unrelated transport,
permission, or malformed-output failures into capability results.

## Decision

Add current and calculated resource-group score operations that return a frozen result envelope.
The envelope records `available` or `capability-unavailable`, the current/calculated mode, the
resolved system, the selector, score items, and an actionable unavailable reason.

Before a score command, read `lshmc -V`. A successful probe reporting a release below V11R1M1110,
or successful output whose version is missing, malformed, or unrecognized, returns
`capability-unavailable` with an upgrade-or-verify-version diagnostic without sending unsupported
resource-group grammar. A nonzero, permission, timeout, or transport failure from the version probe
propagates unchanged. Focused tests distinguish all of those cases from admitted releases. On an
admitted HMC, run `lsmemopt -r resgroup` with an explicit name, ID, or all selector. Translate only
`HSCLCA00` into `capability-unavailable`; propagate every other CLI failure. Use `-F` with an
explicit field list and `--header`, then require the exact current fields
`resource_group_name,resource_group_id,curr_score` or calculated fields
`resource_group_name,resource_group_id,curr_score,predicted_score,requested_lpar_names,
requested_lpar_ids,protected_lpar_names,protected_lpar_ids`. Preserve score strings, including
the `none` sentinel, and mark calculated rows `prediction_guaranteed: false`.

The feature invokes only `lshmc`, `lssyscfg` name resolution, and `lsmemopt`; it never invokes
`optmem` or otherwise controls Dynamic Platform Optimization.

## Consequences

POWER9/POWER10 targets and back-level HMCs produce a stable structured capability response.
Malformed output and non-capability failures remain explicit errors. The public API gains one
selector type, one result type, and two async operations, requiring a minor release under ADR
0029. A version check adds one SSH round trip per operation.

## Considered & rejected

- **Attempt the score command on every HMC and translate every failure.** verified: the
  version-labelled captures on issue #312 show V10 rejects the grammar before the managed system,
  while permission and transport failures share the same nonzero transport path; broad translation
  would hide real failures.
- **Gate on a hard-coded managed-system model list.** judgment: model enumeration rejects future
  Power11 systems and duplicates the HMC's authoritative `HSCLCA00` capability decision.
- **Parse default key/value output.** verified: the issue #312 capture shows calculated default
  output omits the name selector columns that `-F --header` exposes; the explicit projection is the
  only observed complete schema.
- **Expose raw `HMCCLIError` for unsupported systems.** judgment: it fails the requested actionable,
  structured capability contract and makes callers parse vendor prose.
- **Control DPO after calculating scores.** verified: issue #312 excludes DPO control, and IBM's
  `lsmemopt` documentation defines calculated scores as potential rather than achieved results.
