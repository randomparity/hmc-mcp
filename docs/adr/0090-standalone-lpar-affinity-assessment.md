# ADR 0090: Compose affinity assessment into standalone activation results

## Status

Accepted

## Context

Standalone `hmc_power_on_lpar` has a stable result for submitted and already-running requests, but
only composite provisioning can apply the accepted post-activation affinity policy and assessment
contracts from issues #316 and #317. Assessment requires a completed activation plus SSH-backed
score and policy observations, while callers still need the original power result when assessment
cannot run.

## Decision

Add an always-present assessment companion to the existing power-on outcome. It records whether a
measurement occurred, a `skipped`, `passed`, `warned`, `failed`, or `unavailable` status, a reason,
and optional normalized assessment evidence.

Reuse the provisioning assessment request and shared measurement operation. Standalone activation
validates its target binding before mutation and measures only after this call observes a successful
terminal PowerOn result. Already-running and non-waiting calls skip measurement; timeouts and failed
jobs make it unavailable. Explicit `warn` intent reports adverse evidence as warned. Explicit
`fail` intent treats adverse or unavailable assessment as failed without discarding the power job.

## Consequences

Default calls retain their traffic and power semantics while gaining a deterministic skipped
companion. Opt-in calls must wait to observe activation success. Assessment transport or capability
failure remains distinguishable from activation failure, and callers can choose warning or
fail-closed interpretation without the tool raising after a successful power operation.

## Considered & rejected

- **Return a second result shape only when assessment is requested.** judgment: union-shaped output
  makes every caller branch before it can inspect the power result and contradicts the stable-result
  requirement.
- **Assess an already-running partition.** verified: issue #319 requires assessment only after an
  observed successful activation; the existing guard submits no activation job for that path.
- **Force waiting whenever assessment is requested.** judgment: silently changing `wait=false`
  would break the established non-waiting contract; a skipped companion states why measurement did
  not occur.
- **Raise when fail-closed assessment fails.** judgment: raising would hide the completed PowerOn
  result and make remote state harder to reconcile; the explicit `failed` status carries the same
  caller intent without losing evidence.
