# ADR 0056: Evidence-bounded SR-IOV logical-port assignment

## Status

Accepted on 2026-08-20. Supersedes ADR 0053 only for the POWER9/HMC V10R3 M1060 SR-IOV
read projections and mutation cells recorded here.

> **Superseded by [0112](0112-sriov-physical-port-level-selection.md)** (2026-09-01)
> only for physical-port read level selection.

## Context

ADR 0053 left SR-IOV unavailable without same-family live evidence. Issue #214 now carries
sanitized Phase 1 and Phase 2 captures from POWER9 8375-42A under HMC V10R3 M1060. They establish
adapter, RoCE physical-port, Ethernet logical-port, LPAR-state, and profile reads; dynamic
assignment; and profile unassignment. They also expose silent reassignment, non-idempotent duplicate
commands, capacity errors, profile/effective divergence, and an OS-claimed Running port that could
not be removed. No capture proves successful dynamic unassignment of an unclaimed port or
adapter-mode mutation.

## Decision

Admit only the captured family. Adapter inventory uses no `--level`; physical-port inventory uses
`--level roce`; configured logical-port inventory uses `--level eth`; unconfigured identity uses
default key/value output. Preserve identity strings and decimal percentages. `No results were
found.` is available-empty only for admitted reads; other nonzero or malformed results are errors.

Dynamic assign supports `Not Activated` and `Running` with active RMC. Immediately before mutation,
authorize the LPAR and re-read adapter mode/health, physical-port state/capacity, logical-port
ownership, and LPAR state. Same-owner/same-capacity is an idempotent no-op. Foreign owner, wrong
capacity, non-SR-IOV mode, unavailable port, exhausted capacity, Open Firmware, or Running without
active RMC fails before mutation. After success, effective ownership/capacity must match and profile
state must remain unchanged. Failed readback raises a structured partial error.

Profile unassign supports only `Not Activated` and an explicit profile. `none` is an idempotent
no-op. Remove only when the profile contains exactly the selected logical port; refuse ambiguous
multi-port or mismatched records. Set the Ethernet property to `none`, re-read it, and require
`none`. Running dynamic unassign and all other unproved cells are unavailable before mutation.
Never use `--force`.

Replace `set-sriov-mode` in place. It reads adapter state and returns unchanged for the current
mode, but actual mode transitions remain unavailable. Remove the conflicting raw `-o s --id`
helper and provide no alias or second path.

## Consequences

Supported operations expose stable before/after and partial-error records without conflating
profile and effective state. Immediate ownership read is mandatory because the HMC silently moved
a foreign-owned port. Dynamic command duplication is not the idempotency mechanism. Running
unassign stays unavailable, and profile unassign refuses an unproved multi-record rewrite.

## Considered & rejected

- **Trust HMC conflict rejection.** verified: issue #214 Phase 2 Step 3 captured exit 0 and silent
  transfer of logical port `27004002` to another LPAR.
- **Use duplicate command success as idempotency.** verified: Phase 2 Step 2 captured `HSCL1288`
  and exit 1; preflight readback is the oracle.
- **Claim dynamic unassign support.** verified: Phase 2 captured only failure for an OS-claimed
  Running port and no successful unclaimed removal.
- **Rewrite any profile to remove one port.** judgment: the capture proves `none` and one named
  record, so rewriting multiple records would infer serialization.
- **Keep raw adapter-mode mutation.** verified: ADR 0053 records conflicting grammar, and neither
  capture characterizes a successful transition with readback.
- **Keep every SR-IOV operation unavailable.** judgment: this discards complete same-family reads
  and characterized mutation cells.
