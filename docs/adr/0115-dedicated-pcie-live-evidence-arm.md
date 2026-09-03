# ADR 0115: The dedicated PCIe live arm gathers evidence through the documented profile grammar

## Status

Proposed on 2026-09-02.

This record is accepted only once the arm it describes has landed with the guard tests
green — `just verify` and `uv run --no-sync prek run --all-files` — in the form ADRs 0053
and 0055 use ("Accepted on `<date>` after `<what passed>`"). The arm's live PASS/SKIP/FAIL
matrix is the operator's, produced after merge, and is not a precondition of acceptance.

## Context

ADR 0055 makes `assign_dedicated_pcie_slot` and `unassign_dedicated_pcie_slot` fail closed:
they raise `PcieAssignmentUnavailableError` before issuing any mutating command —
`_authorize_pcie_profile_request` resolves and authorizes the target names first, so the
refusal costs one HMC round trip — and
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
state. The identity the guard verified is the identity the delete acts on — the fixture's
UUID, not its name — and the operation layer's own ownership check is left switched on
behind the guards, because the fixture is stamped by the same run and has no need of an
override.

**Every cleanup decision is taken on live state, never on what the run believes it did.**
A local flag can be wrong in the destructive direction: a `chsyscfg` whose response was
lost, or whose confirming read failed, has still mutated the profile. So the removal
branch is entered whenever the profile's current `io_slots` differs from the exact baseline
captured before any mutation, and the removal itself is issued only when that current value
equals the exact value this run applied. Anything else refuses and records recovery
evidence. Hardware is removed before the LPAR is deleted, and the delete is refused
outright while the profile still differs from the captured baseline — that pairing, not
the ordering alone, is what stops a slot being stranded on a partition that no longer
exists.

An identity the guards cannot confirm is never allowed to acquire hardware in the first
place. The arm stops before any mutation, and proceeds directly to cleanup, when the
fixture's UUID cannot be resolved **or** when `hmc_create_lpar` reports the ADR 0064
ownership stamp did not land: the caller token is the fact Guard A checks first and
refuses on unconditionally, so mutating under a partition that carries no token guarantees
the stranded-slot outcome the guards exist to prevent.

Absent configuration, an unavailable capability, and hardware outside the live-verified
envelope are all reported as SKIP for the arm, never PASS. The arm reads the HMC release and
the managed system's type-model before it creates anything, records both as rows so every
results file is self-labelling, and SKIPs when they fall outside the envelope
`operations/pcie.py:require_admitted_environment` enforces for the SR-IOV path.

That gate bounds a risk it cannot remove, and the distinction is load-bearing. ADR 0053
admits the `io_slots` profile-mutation grammar from its **Power8 documentation** row, and the
sole artifact behind it, `tests/fixtures/pcie/power8-profile-contract.json`, records
`hmc_release: not-established` and `support: unknown`. No `io_slots` evidence exists in this
repository for `V10R3 M1060` / `8375-42A` or for any other live-verified envelope, and ADR
0053 states that a field admitted for one family cannot be assumed present in another. So the
arm's first mutating run issues an unprobed grammar whatever it runs on — which is exactly the
gap it exists to close. Confining it to the one pair the repository has live-verified for
anything keeps the blast radius on the machine an operator is already exercising, rather than
letting an arbitrary Power8, Power10 or Power11 system receive a real profile mutation.

## Consequences

An operator running the arm on admitted hardware obtains the exact before/after `io_slots`
readback that ADR 0053 names as its admission condition, plus an assign → verify → unassign
→ verify-restored → reassign → cleanup round trip. That output is the input to a later
capability change; this ADR authorizes no part of that change.

The arm issues raw CLI through the escape hatch, so it is not protected by the operation
layer's ownership and validation checks. The fixture ownership guards above are what
replaces them, and they are the part the unit tests must exercise — a guard that never
refuses is indistinguishable from no guard.

**Guard B's exact string comparison rests on a readback ADR 0053 records as not yet
admitted, and that is a stated expectation rather than an oversight.** `io_slots` is a
list-valued attribute; nothing in this repository establishes that
`lssyscfg -r prof -F io_slots` is byte-stable across an add/remove round trip on a profile
that already holds slots, and no captured sample exists to check against. If it is not, the
post-removal value will not equal the captured baseline, Guard B will refuse the delete, and
the run will end with the fixture alive and recovery evidence recorded. That is the correct
outcome, not a defect: the refusal row carries the baseline and post-removal strings side by
side, so the first live run answers the stability question directly, and the captured
before/after pair is the ADR 0053 input either way. Guard B is not weakened to accommodate
it — a comparison loose enough to tolerate re-rendering is also loose enough to tolerate
third-party drift, which is the thing it exists to catch.

The arm's mutation surface is a partition it created in the same run. It never mutates a
pre-existing LPAR, and it selects only a dedicated slot that inventory reports as
unassigned.

The arm records the create-time capability boundary by asking `hmc_create_lpar` for a
partition with a non-empty `assignments.dedicated`, which `prevalidate_lpar_pcie_assignments`
refuses before `create_and_stamp_lpar` runs. That refusal is load-bearing: it is the only
reason the probe creates nothing. **A capability change that lifts ADR 0055's gate must
revisit this probe**, because on that day the probe stops refusing and starts creating a
second partition. The arm therefore names that partition on its fixture and deletes it in
cleanup under the same caller-token comparison, rather than relying on a gate this very
evidence exists to remove.

## Considered & rejected

- **Build the arm only on `hmc_assign_dedicated_pcie_slot` / `hmc_unassign_dedicated_pcie_slot`.**
  verified: `operations/pcie.py:_authorize_pcie_profile_request` raises
  `PcieAssignmentUnavailableError` unconditionally after argument validation and
  `resolve_and_authorize_lpar_names`, and
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
