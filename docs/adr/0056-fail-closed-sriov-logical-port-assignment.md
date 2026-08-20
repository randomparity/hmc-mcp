# ADR 0056: Fail-closed SR-IOV logical-port assignment

## Status

Accepted on 2026-08-20.

## Context

Issue #214 asks for logical-port assignment keyed by the normalized adapter, physical-port, and
logical-port identities from issue #212. ADR 0053 documents logical-port mutation grammar but
admits no SR-IOV adapter, physical-port, logical-port, owner, capacity, or exact assignment
readback projection. It also identifies the existing `set_sriov_adapter_mode` helper as using a
conflicting command grammar. A mutating implementation cannot validate the requested
preconditions, distinguish a completed retry from a conflict, or verify its result.

## Decision

Replace the raw adapter-mode helper and add symmetric logical-port assign/unassign operations as
presentation-neutral, fail-closed contracts. Each LPAR-targeted operation resolves the system and
LPAR, validates non-blank normalized selectors and positive two-decimal percentage capacity,
enforces ADR 0011 ownership, and then reports capability unavailable before issuing an inventory
or mutation command. Adapter-mode changes likewise validate their selector and requested mode,
then report capability unavailable before command execution.

The assignment result schema keeps requested identity, requested capacity, and separate profile
and effective before/after fields. Those state fields remain unavailable until one supported HMC
family admits all required inventory and readback fields. A retry therefore returns the same
capability error without side effects; it never guesses idempotency from command success.

Remove the executable legacy SSH helper without adding dormant replacement mutation builders.
Documented Power10/Power11 mutation grammar remains evidence in ADR 0053, not callable code, until
version-labelled read projections and exact precondition/readback evidence activate a real path.

## Consequences

The conflicting adapter-mode mutation disappears. MCP, CLI, and Python callers receive explicit,
symmetric operations and stable validation errors, but no real HMC can yet perform these mutations.
A later change can add transport code only after expanding ADR 0053 and the normalized inventory
collectors together. No result claims capacity, ownership, profile state, or effective state that
the HMC was not queried to prove.

## Considered & rejected

- **Mutate using the documented Power10/Power11 command grammar.** verified: ADR 0053 admits the
  mutation selectors but no adapter/port/owner/capacity/readback projection, and explicitly says
  logical-port mutation remains capability-unavailable.
- **Treat vNIC backing strings as assignment state.** verified: issue #214 identifies vNIC backing
  strings as unable to establish logical-port capacity ownership, lifecycle, or readback.
- **Retain the existing adapter-mode helper beside the new contract.** verified: ADR 0053 records
  that helper's `-o s --id` grammar as conflicting with the documented `-o a/r -a slot_id=...`
  contract; keeping both leaves an unsafe public bypass.
- **Omit the public operations until inventory is readable.** judgment: explicit fail-closed
  surfaces preserve the requested selector and error contracts and remove the unsafe legacy path.
- **Do nothing.** judgment: the conflicting adapter-mode mutation would remain callable.
