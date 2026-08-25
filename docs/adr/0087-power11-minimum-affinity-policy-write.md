# ADR 0087: Write Power11 minimum-affinity policy through HMC CLI

## Status

Accepted

## Context

Issue #316 requires an authorized setter for the Power11 minimum-affinity policy and optional
provisioning propagation. The checked-in Power11 `chsyscfg` and `mksyscfg` references document
`min_affinity_score` (0–100) and `min_affinity_score_action` (`none`, `warn`, or `fail`) as
partition attributes. The Power11 REST corpus contains no corresponding logical-partition field.
ADR 0086 established advertised `POWER11` processor compatibility as the evidence-backed
capability gate and deliberately excluded mutation.

## Decision

Add one shared policy value and one SSH mutation helper. Validate the complete policy before any
HMC call, probe `lpar_proc_compat_modes`, reject systems that do not advertise `POWER11`, then run
`chsyscfg -r lpar` with both documented attributes in one quoted input record. The public operation
resolves the target and applies the existing LPAR ownership authorization before dispatching the
SSH mutation. `fail` has no default: callers must pass it explicitly as part of a complete policy.

Provisioning accepts an optional policy. When present, it validates and capability-probes before
the first mutation, creates and ownership-stamps the partition through the existing REST path, then
applies the policy through the same authorized SSH operation before network, storage, assignment,
or power-on steps. Omission preserves all current defaults and performs no additional probe or
mutation. Snapshot capture continues to preserve the observed policy, but no snapshot or profile
application API is added because none exists in the repository and IBM documents these fields as
partition attributes rather than profile attributes.

## Consequences

Unsupported or malformed requests fail before mutation. A failure applying a requested policy
after partition creation is reported as a partial provisioning failure; the created partition is
not rolled back. The implementation uses only documented CLI fields and adds no REST vocabulary.
Callers that want `fail` must deliberately select it. Native Power11 live execution remains an
unexecuted verification arm.

## Considered & rejected

- **Add the policy to REST create/modify documents.** verified: `rg -li
  'min_affinity_score|minAffinityScore|MinimumAffinity' docs/refs/hmc-rest-api-p11` returns no
  matching REST property, while the local Power11 CLI references document both fields.
- **Treat the policy as an LPAR profile attribute.** verified: the Power11 `mksyscfg` and
  `chsyscfg` references list both fields among partition attributes, before the separate profile
  and common-attribute sections.
- **Default the action to `fail` when a score is supplied.** judgment: failure changes workload
  activation behavior and issue #316 requires deliberate caller selection.
- **Create first and discover capability while applying.** judgment: it violates the required
  fail-before-mutation contract and leaves an avoidable partial resource on unsupported systems.
- **Add snapshot application now.** verified: `rg -n 'apply_lpar_snapshot|snapshot.*apply' src
  tests` finds no application surface to extend; inventing one would exceed this issue.
