# ADR 0055: Fail-closed dedicated PCIe assignment

## Status

Accepted on 2026-08-20.

## Context

The existing public profile helper appends `io_slots` with unconditional `--force`. ADR 0053
admits dedicated-slot inventory and documents dynamic and profile mutation grammar, but admits
neither effective-state nor profile-state readback. Issue #213 requires symmetric, idempotent
assignment contracts that enforce that capability matrix rather than guessing after mutation.

## Decision

Replace the raw profile helper with presentation-neutral assign and unassign operations keyed by
the normalized `drc_index` selector and an explicit profile name. Each operation resolves the
target LPAR and enforces its ADR 0011 description-token ownership independently from slot
occupancy. An absent token remains an advisory no-claim, a malformed or foreign token blocks, and
`ownership_override=true` is accepted only as the caller's record of operator approval.

The result contract models profile and effective ownership separately. Because profile state is
unavailable, no observed effective owner can prove that a profile request is already complete.
Every assign or unassign therefore fails with a capability-unavailable error before issuing a
command, including retries, until exact profile readback is admitted by ADR 0053.

Keep symmetric, record-safe SSH command functions for the documented profile grammar, without
`--force`, as the bounded transport contract for later evidence work. They are not selected by the
public operation until a version-labelled evidence record admits exact profile readback. Foreign
ownership and ambiguous selectors fail closed.

## Consequences

The unsafe mutation path disappears and callers receive the same explicit capability error for
safe retries. No assignment can yet mutate a real HMC; supporting that requires new
evidence and a later ADR 0053 capability update. No operation claims profile/effective convergence
that it cannot read.

## Considered & rejected

- **Continue using `--force` and trust command success.** verified: ADR 0053 states that neither
  dedicated-slot profile nor effective mutation has admitted exact readback, so success cannot
  establish the requested state.
- **Mutate without force and return the preflight inventory as the result.** judgment: that would
  present an unverified external side effect as a stable before/after result.
- **Remove all assignment entry points.** judgment: explicit fail-closed operations preserve a
  coherent selector and idempotency contract while making current capability limits observable.
- **Do nothing.** judgment: the existing public helper remains an unconditional conflict override.
