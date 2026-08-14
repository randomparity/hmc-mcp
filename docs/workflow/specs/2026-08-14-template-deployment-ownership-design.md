# Template deployment ownership stamping design

**Issue:** [#135](https://github.com/randomparity/hmc-mcp/issues/135)  
**Decision:** [ADR 0014](../../adr/0014-conservative-template-deployment-ownership-inference.md)

## Goal

Complete ADR 0011 Phase 1 for `hmc_deploy_partition_template`: a successfully awaited
template deployment should stamp its new LPAR when list-diff evidence identifies exactly
one partition, without making best-effort ownership metadata a deployment failure.

## Architecture

`operations_templates.deploy_partition_template` owns the orchestration. For `wait=True`,
it obtains a system-scoped LPAR baseline before submission, waits through the existing job
helper, and only after a `COMPLETED` terminal result obtains the second snapshot. A pure
selection helper compares top-level parsed-feed `UUID` values and returns the sole new
entry or explains why identification is inconclusive.

The existing ownership operation in `operations_lpar` remains responsible for resolving
the managed-system name and calling the SSH stamp. Its private helper becomes a named
presentation-neutral operation so the template workflow can reuse exactly the same
`True`/`False`/`None` and warning semantics as direct creation.

## Data flow and result contract

1. Validate wait timing and resolve the managed-system UUID as today.
2. With `wait=False`, submit and return immediately. Return
   `ownership_stamped=None` and the existing manual-identification warning; do not list or
   stamp.
3. With `wait=True`, request `list_logical_partitions(system_uuid)` before submission.
   If it fails, retain an unavailable-baseline marker and continue deployment.
4. Submit and wait using the current helpers. If the selected job is absent or its
   `Resource.Status` is not exactly `COMPLETED`, return it without a post-list or stamp,
   with `ownership_stamped=None` and a reason-specific warning.
5. After `COMPLETED`, list again. Compare valid, non-empty string UUIDs against the
   baseline. Exactly one new entry is the stamp target. Zero, multiple, malformed, or
   unavailable snapshots produce `ownership_stamped=None` and a warning that says why no
   safe target was selected.
6. Pass the selected entry, resolved system UUID, and original system selector to the
   shared ownership-stamp operation. Return its status and warnings unchanged.

Every result contains `job`, `ownership_stamped`, and `warnings`. A stamp succeeds with
`True` and no ownership warning; an attempted stamp failure returns `False`; no attempt
returns `None`.

## Failure handling

LPAR observation is part of best-effort metadata, not template deployment. Catch HMC API
errors from either snapshot, log enough server-side context for diagnosis, and return an
actionable warning without including credentials or response bodies. Do not catch job
submission, polling, or timing errors already surfaced by the existing deployment path.

The workflow never guesses among candidates. This is the concurrency invariant governed
by ADR 0014 and the smallest behavior that satisfies issue #135 without risking ownership
misattribution.

## Documentation

Remove statements in `server_templates`, `_app.py`, and ADR 0011 that claim template
deployment never stamps. Replace them with the exact `wait=True` and unambiguous-diff
condition. ADR 0011 remains accepted; its known-gap consequence becomes a link to the
resolving issue and ADR rather than being rewritten.

## Testing

Focused tests must prove:

- `wait=False` performs no LPAR list or stamp and reports `None`;
- a completed waited deployment with exactly one new UUID attempts the expected name and
  returns `True` on stamp success;
- stamp failure returns `False` and a warning without failing deployment;
- zero and multiple new UUIDs do not stamp and return distinct actionable warnings;
- a baseline or post-list HMC error does not fail a successful deployment;
- a non-completed terminal job performs no post-list or stamp;
- entries without a usable UUID are never selected by name alone.

Run the focused template and ownership tests during TDD, then `just verify` and
`uv run prek run --all-files` before shipment.

## Scope boundaries

No tool inputs, token format, hard enforcement, persistence, dependency, job behavior, or
other create workflow changes are included. Python remains `>=3.12`; no new dependency is
introduced. The target architectures remain undeclared by repository policy, and this
logic is architecture-independent.

## Threat model

The workflow changes advisory ownership attribution, so another local agent or operator
creating an LPAR concurrently is the relevant untrusted actor. The added REST snapshots
cross the existing authenticated HMC boundary; the design adds no new entry point and
widens no credentials or permissions.

UUID-only set comparison controls the attribution boundary, and the exactly-one invariant
prevents ambiguous data from reaching the existing SSH description writer. Existing
`HMCConfig` credential handling, partition-name validation in the stamp path, and
best-effort exception handling remain the controls at the REST and SSH boundaries.
Warnings expose only candidate counts or operation context, never credentials, raw HMC
responses, or ownership tokens belonging to other partitions.

Hard isolation from another HMC user, malicious callers that bypass MCP guidance, and
enforcement of ownership on mutation remain explicitly outside ADR 0011 Phase 1 and this
issue.
