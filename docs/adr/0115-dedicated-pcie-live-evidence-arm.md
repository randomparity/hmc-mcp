# ADR 0115: The dedicated PCIe live arm gathers evidence through the documented profile grammar

## Status

Accepted on 2026-09-02.

## Context

ADR 0055 makes `assign_dedicated_pcie_slot` and `unassign_dedicated_pcie_slot` fail closed:
they raise `PcieAssignmentUnavailableError` before issuing any command, and
`_analyze_assignment_requests` does the same for a create-time
`LparPcieAssignments.dedicated` request. ADR 0053 names the single condition that lifts
that gate — "Dedicated-slot profile grammar is recorded, but profile mutation likewise
remains capability-unavailable until exact `io_slots` readback is admitted."

Issue #217 asks for live proof that dedicated PCIe assignment is reversible. Its SR-IOV
half landed in PR #603 and was verified on real hardware on 2026-09-01. The dedicated half
has no code at all, so it cannot even emit the per-arm SKIP the issue requires.

A live arm built only on the admitted operations can produce nothing but a capability
refusal, on every run, on every machine. It would prove that the gate is closed — which the
unit tests already prove — and would never produce the `io_slots` readback evidence that is
the stated precondition for opening it.

## Decision

The dedicated arm in `scripts/live_test/pcie.py` records the admitted operation's
capability refusal as an explicit SKIP row, and then gathers the reversibility evidence
through the ADR 0053-documented profile grammar issued over the live runner's already
opted-in `hmc_run_command` escape hatch: `lssyscfg -r prof -F io_slots` to read, and
`chsyscfg -r prof -i "name=<profile>,io_slots±=<drc>//0,lpar_name=<lpar>"` to mutate.

The arm is an instrument, not a contract change. It does not modify, weaken, or bypass the
fail-closed operations in `src/`; ADR 0055's gate stands exactly as written, and lifting it
remains a separate change requiring an ADR 0053 capability update grounded in what this arm
observes.

Every mutation the arm issues is bounded by a fixture it created and can prove it still
owns: a run-unique LPAR carrying this run's ADR 0064 caller token, whose UUID and token are
re-read immediately before each cleanup action and compared exactly before any mutation is
issued. A mismatch refuses to mutate, records manual-recovery evidence naming the exact
command an operator must run, and stops the cleanup rather than continuing over an unknown
state. Hardware is removed before the LPAR is deleted, so a failed removal cannot strand a
slot on a partition that no longer exists.

Absent configuration, absent compatible hardware, and an unavailable capability are all
reported as SKIP for the arm, never PASS.

## Consequences

An operator running the arm on admitted hardware obtains the exact before/after `io_slots`
readback that ADR 0053 names as its admission condition, plus an assign → verify → unassign
→ verify-restored → reassign → cleanup round trip. That output is the input to a later
capability change; this ADR authorizes no part of that change.

The arm issues raw CLI through the escape hatch, so it is not protected by the operation
layer's ownership and validation checks. The fixture ownership guards above are what
replaces them, and they are the part the unit tests must exercise — a guard that never
refuses is indistinguishable from no guard.

The arm's mutation surface is a partition it created in the same run. It never mutates a
pre-existing LPAR, and it selects only a dedicated slot that inventory reports as
unassigned.

## Considered & rejected

- **Build the arm only on `hmc_assign_dedicated_pcie_slot` / `hmc_unassign_dedicated_pcie_slot`.**
  verified: `operations/pcie.py:_authorize_pcie_profile_request` raises
  `PcieAssignmentUnavailableError` unconditionally after argument validation, and
  `operations/lpar/assignments.py:_analyze_assignment_requests` raises the same for any
  non-empty `assignments.dedicated`, both at `main@51d190c7`. Every run would be SKIP and no
  `io_slots` evidence would ever be produced.
- **Lift ADR 0055's gate in `src/` as part of this change.** judgment: the evidence that
  would justify lifting it does not exist until this arm has run on hardware, so the change
  would be admitting a capability on the strength of the instrument built to measure it.
- **Reuse the existing `ltczz386-lp3` partition, as the SR-IOV arm does.** verified: issue
  #217 requires "a run-unique owner-stamped LPAR" and deletion "only after
  UUID/run-marker/ownership comparisons prove this run still owns it"; lp3 predates the run
  and carries no run marker, so no comparison could establish ownership.
- **Add `--force` to the profile mutation to avoid conflict failures.** verified: ADR 0055
  removed exactly that, recording the prior helper's unconditional `--force` as an
  unconditional conflict override. A refusal from the HMC is the signal the arm exists to
  record.
- **Do nothing and let the operator drive the CLI by hand.** judgment: the guards, the exact
  baseline comparison, and the manual-recovery evidence are the reviewable part; a hand-run
  session produces none of them and cannot be unit-tested.
