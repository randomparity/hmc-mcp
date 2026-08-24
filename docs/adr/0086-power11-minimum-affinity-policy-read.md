# ADR 0086: Read Power11 minimum-affinity policy through HMC CLI

## Status

Accepted

## Context

Power11 partition configuration adds `min_affinity_score` and
`min_affinity_score_action`. The checked-in Power11 `mksyscfg` and `chsyscfg` references name
those fields and define their value domains, while the Power11 REST references contain no
equivalent logical-partition properties. Version-labelled evidence on issue #315 shows the explicit
read projection succeeds on HMC V11R2M1120 systems whose advertised compatibility includes
POWER11, and fails as an invalid attribute on POWER10/POWER9 compatibility levels and HMC V10.
The successful systems used 000B firmware on Power9 hardware; native Power11 hardware was not
available in an authenticated state.

Callers need a read-only policy result that distinguishes unsupported systems from malformed or
unrelated HMC failures and that can be embedded in portable snapshots without making capture fail
on older systems.

## Decision

Use SSH `lssyscfg -r lpar` with an explicit `-F min_affinity_score,
min_affinity_score_action --header` projection as the policy read path. Before that projection,
read the managed system's `lpar_proc_compat_modes`. A list containing `POWER11` admits the policy
query; a successful list without `POWER11` returns capability-unavailable with an actionable
firmware/compatibility reason. Malformed capability output and other probe failures propagate.

Return a frozen envelope containing `available` or `capability-unavailable`, the resolved system and
LPAR names, nullable policy values, and an unavailable reason. Available rows require exactly one
row, an integer score from 0 through 100, and one action from `none`, `warn`, or `fail`. Empty,
duplicate, out-of-range, or unknown values are malformed HMC output. No setter is added.

Portable snapshots add a version-1 `minimum-affinity-policy` capability. Supported captures store
the validated policy as an observation; unsupported captures retain the snapshot and record the
capability as unsupported with the same reason.

## Consequences

The CLI is the only evidence-backed read surface. Supported POWER11-compatibility reads add the
capability probe and policy query SSH round trips. Power10/POWER9 compatibility and back-level HMC
users receive structured absence rather than a failed read or snapshot. Native Power11 hardware
remains an unexecuted live-test arm; the design does not substitute a type-model claim for it. The
Python API, MCP, and CLI gain one read operation and no mutation.

## Considered & rejected

- **Read the policy from REST logical-partition resources.** verified: `rg -li
  'min_affinity_score|minAffinityScore|MinimumAffinity' docs/refs/hmc-rest-api-p11` finds no LPAR
  property, while the checked-in Power11 CLI references define both fields.
- **Infer Power11 from a machine-type list.** verified: issue #315's successful captures use Power9
  type-models with 000B firmware and advertised POWER11 compatibility, while lower compatibility
  levels reject the field; hardware type does not describe the observed capability.
- **Reuse resource-group availability as the gate.** verified: ADR 0084's `HSCLCA00` means the
  system lacks multiple resource groups, which is topology evidence rather than direct evidence
  about partition policy fields.
- **Attempt the policy projection and translate any CLI error as unsupported.** verified: issue
  #315 shows a direct successful compatibility probe before the projection; broad translation would
  still hide transport, permission, and malformed-output failures.
- **Add a setter with the reader.** verified: issue #315 explicitly excludes mutation.
