# ADR 0136: Safe VolumeGroup write reconciliation

## Status

Accepted (2026-09-13)

## Context

HMC V10R3 can return a 5xx after a VolumeGroup mutation has been dispatched.
Retrying can duplicate or otherwise compound a destructive storage operation.
The client currently exposes only the original HTTP error, leaving callers
without the before/after state needed to decide how to recover.

## Decision

For the bounded VolumeGroup and VIOS mutation inventory in the accompanying
design, capture the relevant VolumeGroup or VIOS inventory before dispatch.
On a 5xx, perform one best-effort readback and raise an error that identifies a
possible side effect, includes the observed comparison or readback failure, and
explicitly tells the operator not to retry until state is verified. Never retry
a 5xx. Reconciliation is diagnostic only and makes no rollback attempt.

## Consequences

The failure path performs one additional read and produces an actionable error.
Successful writes and non-5xx failures keep their current behavior. The policy
does not extend to storage mutations outside the audited inventory.

## Considered & rejected

- **Retry failed writes automatically.** verified: issue #779 records physical-volume
  metadata loss after an HTTP 500, so a second mutation is unsafe.
- **Return the original error without readback.** judgment: it leaves the operator
  unable to distinguish a rejected request from a mutation that took effect.
- **Attempt rollback after a failed write.** judgment: no generic rollback can
  safely reconstruct the prior physical-volume or VolumeGroup state.
