# ADR 0166: Dedicated PCIe assignment never writes the read rendering, and the live arm's create-time probe asserts the lifted gate

## Status

Accepted on 2026-09-22 for issue #882. No live run has exercised this change; live verification
belongs to the release live window (#879) and needs operator authorization.

## Context

ADR 0165 admitted exact `io_slots` readback — `lssyscfg -r prof -m SYS -F
lpar_name,name,io_slots --header`, triples rendered `drc/none/is_required` — inside the HMC
`V10R3 M1060` / model `8375-42A` envelope, and put two preconditions on the change that consumes
it: enforce that envelope before any mutating command, and establish that a value read back in
that rendering is accepted as `chsyscfg` input before issuing one. No capture has fed a read
value back. The documented input grammar writes the same unset pool **empty** (`21030003//0`,
`docs/refs/hmc-commands-p11/commands/mksyscfg.md:69`); the read renders it `none`.

ADR 0163's Consequences add a third obligation: once the gate lifts, the arm's create-time probe
"stops refusing and starts creating a second partition", and its two `ExpectedOutcome` SKIP
declarations (`scripts/live_test/pcie.py`, `_DEDICATED_CREATE_TIME_UNAVAILABLE` and
`_DEDICATED_ASSIGN_UNAVAILABLE`) stop describing anything the operations do.

## Decision

**1. The second precondition is narrowed, not satisfied.** No read or parsed `io_slots`
value ever reaches a builder: the operations write only the documented input grammar,
`io_slots+=<drc>//0` / `io_slots-=<drc>//0`, through the existing no-`--force` builders, from a
caller-supplied DRC index in the admitted form (eight uppercase hexadecimal digits, as every
captured and documented index renders). What remains is an **unverified** assumption in the
decide step: that a stored element rendered `drc/none/0` is the element `<drc>//0` adds and
removes. Assign treats
that form as already present and does nothing; unassign sends `io_slots-=<drc>//0` at it, and
that includes an element this tool did not write. A DRC present in any other form
(`is_required=1`, a pool, anything unparsed) is refused before mutation, because the assumption
would stretch further there. Readback verification (decision 2) bounds the damage and does not
prove the equivalence: if the assumption is wrong, the result is a refusal or a
`PcieAssignmentPartialError` carrying the profile's actual value, and never a reported success
on an unverified profile.

**The `is_required=1` case.** The capture stores the VIOS partition's slots as
`21020013/none/1`. What `io_slots-=<drc>//0` does to an element stored with `is_required=1` is
unknown: it could remove it, leave it (no-op), or fail. The operations never send it there,
because a DRC present as `drc/none/1` is refused before any write, by assign and unassign
alike. Unit tests over the mocked transport pin each of the three branches as unreached and
fault-inject the refusal. One path still reaches the question: a concurrent writer that turns
the slot required between the read and the write (failure-model class 3). A no-op or a refused
command then reads back as neither state and raises `PcieAssignmentPartialError`. A removal
reads back as the requested absence and reports success, because the profile then holds what
the caller asked for.

**Supersession.** This record supersedes one sentence of ADR 0165 Decision 3 — "#882 must also
establish that a value read back in this rendering is accepted as `chsyscfg` input — the capture
never fed one back — before issuing any mutating command" — as a precondition on mutation. It
replaces that sentence with decision 1's two rules: never write a read value, and verify by
readback. It also supersedes ADR 0163's decision to record the admitted operation's capability
refusal as a SKIP row (decision 4 below). The rest of both records stands.

**2. Verification is an exact, order-insensitive comparison of parsed triples.** After the
write the profile's triples must equal the before-state plus or minus `drc/none/0`, as sorted
tuples. The readback runs even when the builder raised, because a lost response may still have
mutated. The outcome is classified by what the readback shows. It equals the intended state:
success. It equals the before-state after a builder error: that error is re-raised, since nothing
changed. Anything else, including a failed readback: `PcieAssignmentPartialError` carrying both
values. The parser accepts literal `none` as an unset pool and as the whole value of an empty profile, and refuses
an empty pool position, an empty value, a malformed triple or a repeated DRC as unadmitted.

**3. The envelope is `require_admitted_environment`'s pair, checked after ADR 0011
authorization and before any profile read or write.** Outside it both operations and create-time
prevalidation raise `PcieAssignmentUnavailableError`, which is kept with that one meaning. The
pair is matched on `lshmc -V`'s own `Version`, `Release` and `Service Pack` fields exactly, not
by the SR-IOV predicate's substring test, which also passes a later service pack that still
lists an M1060 fix line. Its reason string, which ADR 0165 showed to be false, is rewritten to
name the envelope. Inside it
the LPAR must be `Not Activated`, the one state the PCIe state matrix admits profile-only
mutation for, and assign refuses a slot that a profile of another LPAR already lists, since two
partitions contending for one slot at activation is a state no evidence characterizes. The
holder check runs again on the post-write readback, so a concurrent assign to another LPAR is
reported as `PcieAssignmentPartialError` rather than as success.

**4. The live arm's create-time probe becomes an asserted create-time assignment, cleaned up
before the fixture exists.** Neither SKIP declaration survives. ST30 requests create-time
assignment with no expected refusal, records whether the probe's profile lists the DRC, then
removes the slot — only if the probe's live `io_slots` lists it — confirms its absence, and
deletes the probe by UUID, all under the existing caller-token comparison, before the fixture
is created. A probe cleanup that does not complete stops the arm before the fixture is created,
so the slot is never in two profiles at once. ST31 assigns through
`hmc_assign_dedicated_pcie_slot` and no longer issues the raw `io_slots+` after it. Unassign,
reassign, cleanup and every read keep the ADR 0163 escape-hatch grammar.

## Consequences

- **What remains unestablished.** Four things are unestablished. Live acceptance of
  `<drc>//0` add and remove on the envelope. That the HMC renders the written slot as
  `drc/none/0`. That `io_slots-=<drc>//0` matches a pre-existing `drc/none/0` element. That an
  empty profile renders as `none` in the admitted form: the capture holds no empty profile, and
  the whole-value `none` comes from the arm's unadmitted single-field read. Create-time
  assignment on a fresh partition depends on that last rendering first, and ST30 is its first
  exercise. An unexpected rendering fails closed. Before the write, the operation refuses with
  no mutation. After the write, it raises `PcieAssignmentPartialError`. That includes removing a
  profile's last slot when the empty profile does not render as `none`.
- **A failure after the write leaves an unknown profile.** Refusing after `chsyscfg` has run
  is not the same fail-closed as refusing before it. A `PcieAssignmentPartialError` means the
  command may have run. The profile may hold the change, none of it, or a form the operations
  refuse, or it may not parse at all, and from then on both operations refuse that DRC or that
  profile. The error names what to do next. It carries the before and after `io_slots`
  values. It gives the exact admitted read command to inspect the profile. Its advice follows
  what the readback shows for this operation's DRC, not the direction the operation asked
  for. A readback that could not be read or parsed, or that lists the DRC in a form other
  than `drc/none/0`, gets the HMC UI and no command. A DRC that reads back in its before
  state gets "needs no reversal". Only a DRC that shows this operation's effect gets the
  documented `<drc>//0` reversal, for the caller's validated DRC. The error never tells the
  operator to paste the read rendering back as `io_slots=` input. Where another LPAR's
  profile also lists the slot, it names that LPAR but points the operator at this profile:
  that profile was neither written by the tool nor authorized by ADR 0011, so it is left to
  its owner. The tool cannot undo a change it cannot verify. ADR 0165's VIOS-only and populated-profile
  byte-stability limits also stand. Decision 2 tolerates reordering and refuses re-rendering.
- **Some refusals come after earlier workflow legs commit.** Create, provision and modify
  prevalidate only the selectors and the envelope. The LPAR-state, holder, profile and
  other-form refusals run in the dedicated step, after the partition is created or the earlier
  modify legs are applied. The workflow result reports them as an error step, and nothing is
  rolled back. For the live arm, this means the operator must configure a slot that no
  profile lists. A slot listed by another partition's profile, even an inactive one, makes
  ST30 and ST31 FAIL by design.
- **Reach is narrower than the HMC's.** A slot the operator added as required, or in a pool, is
  refused rather than handled. Widening that needs a capture that feeds a read value back.
- **The live question moves to #879 by name.** Before the maturity of
  `pcie.assign_dedicated_slot` or `pcie.unassign_dedicated_slot` is promoted from `unrecorded`,
  the #879 live window must exercise assign and unassign on this path. That run must include an
  element stored with `is_required=1`, to observe what `io_slots-=<drc>//0` does to it. The
  operations refuse that form, so this observation needs a raw `chsyscfg` over the escape
  hatch. Run it against a disposable, non-VIOS profile created for the run, never against the
  VIOS profiles the capture shows holding required slots. Until then, maturity stays
  `unrecorded`. The obligation is recorded here. #879's issue body does not yet carry it.
- **The arm now exercises the operations.** Its ST30 and ST31 rows are evidence about
  `src/` behaviour on the commit that ran, and a FAIL there is a finding, not an expected
  refusal. Its own `io_slots` read stays the `--filter` single-field form ADR 0165 did not admit;
  retiring the escape-hatch path is #876's.
- ADR 0092's remark that these operations are "currently inert" is no longer true; its
  classification of them as guarded still holds.

## Considered & rejected

- **Satisfy precondition 2 with a live round-trip probe first.** verified: the frozen scope
  charter on #882 (`WORK:SCOPE` `q882-dc00cf01`) excludes any live HMC access or mutation, and
  assigns it to the orchestrator, with operator authorization only. Decision 1 keeps the
  question off the write path, and readback bounds the part it cannot remove.
- **Write back the read rendering (`<drc>/none/0`) so reads and writes share one form.**
  verified: ADR 0165 Consequences — "`none` in the pool position is documented nowhere on the
  input side"; this is exactly the unproven input.
- **Remove any triple for the DRC regardless of its read form.** judgment: correctness — the
  removal input `<drc>//0` would then have to match stored elements it was never shown to
  match, and a silent no-op would only surface as a partial error after the fact.
- **Compare the raw `io_slots` string byte for byte.** judgment: fit — ADR 0165 leaves
  reordering open, and a parsed-triple comparison still refuses every content change.
- **Drop the create-time probe.** judgment: cost — create-time assignment is one of the three
  paths this change opens, and the probe is the only live exercise of it.
- **Create the fixture itself with `assignments.dedicated` and delete the probe.** verified:
  ADR 0163 Guard B captures its baseline after the fixture is created and before any mutation
  (`scripts/live_test/pcie.py:create_dedicated_fixture`). That baseline would then already hold
  the slot, so cleanup would delete a partition that still lists it. The frozen criterion 7
  also names the second partition's cleanup.
- **Keep the probe alive until final cleanup.** judgment: correctness — the fixture would then
  request the same slot while another profile lists it, a conflict no evidence characterizes.
- **Remove `PcieAssignmentUnavailableError` outright.** judgment: fit — the envelope still needs
  a capability-unavailable error, and a new name for the same meaning is churn.
- **Do nothing.** verified: ADR 0165 Decision 4 names #882 as owning the wiring; leaving it keeps
  a reason string ADR 0165 records as false.
