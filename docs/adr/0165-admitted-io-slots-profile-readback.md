# ADR 0165: Exact `io_slots` profile readback is admitted from a live capture, inside the SR-IOV envelope

## Status

Accepted on 2026-09-22, on the live capture published at
[#881 comment 5779662835](https://github.com/randomparity/hmc-mcp/issues/881#issuecomment-5779662835).
No runtime behaviour changes with it: `git diff --name-only $(git merge-base HEAD origin/main)
-- src/` prints nothing, the dedicated PCIe operations still fail closed, and the gate stays
shut until #882. On the completed branch `just verify` exits 0 (3m04s) and
`uv run --no-sync prek run --all-files` passes every hook.

## Context

ADR 0053 sealed dedicated-slot profile mutation on one named condition — "profile mutation
likewise remains capability-unavailable until exact `io_slots` readback is admitted"
(`:80-81`) — and ADR 0055 made every assign and unassign fail closed on the same
condition (`:33-34`, `:37-38`). The condition is a documentation condition in form and could not be
met in fact. `io_slots` is documented input-side only: `rg -c io_slots docs/refs` reports it
in `hmc-commands-p{10,11}/commands/chsyscfg.md` and `mksyscfg.md` and returns **no match** in
either `lssyscfg.md`, and the IBM Power8 `lssyscfg` page — already an admitted read-side
locator family through `tests/fixtures/pcie/power8-profile.json` — does not mention it
either. ADR 0053 (`:46-47`) forbids admitting a field because another family documents it.

That corpus is the gitignored, operator-host-only vendored reference AGENTS.md describes,
linked into a worktree by `scripts/link_reference_corpus.py`. It is cited here the way
AGENTS.md requires — by path and by what a search over it returns — and it grounds only a
*negative*: that no read-side locator exists. The public IBM page above confirms the same
negative independently, and the admission below rests on neither. That is the difference
between this citation and the operator-held results document rejected in Decision 2, which
would have had to ground a *positive* capability claim.

So the gate could only be opened by a capture, and on 2026-09-22 an operator took one
against a lab HMC and published it redacted, with a per-probe exit status and stream table.
Its decisive part is the **negative control**:

    lssyscfg -r prof -m <system> -F bogus_attr_xyz --header
    → exit 1, stdout: "An invalid attribute was entered.  The invalid attribute is
      bogus_attr_xyz.  Please correct your entry and retry the command.", stderr empty

The HMC validates `-F` attribute names and rejects an unknown one *by name*, non-zero. That
is what makes `io_slots` being accepted, and returning data, positive evidence rather than a
silent no-op — and it is the thing documentation could never have established.

A second observation points the same way. The corpus renders an unset pool ID as **empty**
in its `mksyscfg` examples — `21030003//0` at
`docs/refs/hmc-commands-p11/commands/mksyscfg.md:69` and `2105001B//0` at `:80` — while the
capture's read path renders the same position as the literal **`none`**. The field layout
matches the documented input grammar; the rendering of an absent pool does not. The input
grammar was therefore never the read contract, which is exactly what ADR 0053's
no-cross-family rule and the state matrix's "do not compose" clause were protecting.

## Decision

**1. The readback is admitted, as captured.**
`tests/fixtures/pcie/power9-v10r3m1060-live-ioslots.json` is a `live-capture` record
(`support: "captured"`) on the `power9-v10r3m1060-live-sriov.json` precedent, citing that
comment as its `source_url`. What is admitted is the command the capture actually issued:

    lssyscfg -r prof -m SYSTEM -F lpar_name,name,io_slots --header

Its `io_slots` column is a comma-separated list of three-position, `/`-separated triples
inside one double-quoted field. What the capture establishes is the **shape**: three positions,
the first a DRC index matching the slots on the system, the second the literal `none` in every
captured triple, the third `0` or `1`. The position *names* `drc_index/pool_id/is_required` are
the documented input-side ones, used here as labels for positions the capture shows — not as
read-side semantics admitted by it, which composing the input grammar is exactly what Decision
2's rejected alternatives refuse. No captured slot belongs to a pool, so whether a pooled slot
renders its pool ID in position two is not established here. Three forms are **not** admitted by
this capture and must not be inferred from it: the single-field `-F io_slots`, a read without
`--header`, and a `--filter`-narrowed read. That matters concretely —
`scripts/live_test/pcie.py:profile_io_slots_command` builds
`lssyscfg -r prof -m SYS --filter … -F io_slots`, which is all three at once, and #882 must
either issue the admitted form or obtain a capture of the one it issues. The record is
**not** a `documented` record and claims no documentation locator.

**2. The operator-held 2026-09-21 results document is not admissible evidence for this
admission.**
ADR 0053 (`:33`) — "the evidence is documentation-backed; it is not a live-HMC
capture" —
describes ADR 0053's own evidence base, which this record does not join; it joins the
live-capture class that already exists beside it. The question the issue raised therefore
changes shape but keeps its answer: that document is git-ignored (`.gitignore:1-2`) because
it carries raw HMC-derived rows, so it can supply no `source_url` a reviewer can open and
nothing a reviewer can falsify. It may not be cited as a `source_url` and may not corroborate
this record. This is a rule about *admissible capability evidence*, not a claim that the run
never happened: ADR 0162's Status already relays that run's outcome as a tracked historical
fact, and ADR 0163's Status relays it after this change. Those are records of history, and
neither admits a capability.

**3. Dedicated profile mutation is confined to the SR-IOV envelope.**
The admitted pair is HMC release `V10R3 M1060` and managed-system model `8375-42A` — the
pair `require_admitted_environment` already enforces for SR-IOV
(`src/hmc_mcp/operations/virtualization/pcie.py:41-42`, `:295-306`). Issue #882 must enforce
that same pair on the dedicated path **before issuing any mutating command**. Outside it the
operations stay capability-unavailable unconditionally, and widening it takes a new capture
on the new pair, not an argument from this one.

**4. Supersession.** This record supersedes exactly one sentence of ADR 0053 — "Dedicated-slot
profile grammar is recorded, but profile mutation likewise remains capability-unavailable
until exact `io_slots` readback is admitted" (`:80-81`) — and the gate conditions of ADR
0055 (`:33-34`, `:37-38`). The sentence immediately above it (`:78-79`), which seals
`chhwres -r io` dynamic grammar until one family admits both that grammar and exact
readback, is untouched and stays with #873. Their condition — *until exact `io_slots`
readback is admitted* — is met, inside the envelope. What keeps the operations failing
closed today is no longer missing evidence but missing code: nothing in `src/` reads
`io_slots`, and #882 owns selecting the builders, wiring the readback, and enforcing
decision 3. Until that lands, every assign and unassign still fails closed, and this record
authorizes no part of that change.

## Consequences

Three limits ride with the admission and are not settled by it.

- **VIOS-only.** The captured system carries two profiles, both on the VIOS partition. This
  admits the readback; it does not exercise dedicated-slot assignment on a non-VIOS
  partition, and #882 cannot read it as having done so.
- **Byte-stability on an already-populated profile is open.** The 2026-09-21 run recorded by
  ADR 0162 exercised an add/remove round trip on a profile that run created, and its teardown
  "returned the system to a byte-identical baseline with no operator action" (ADR 0162), so
  ADR 0163's Guard B did not refuse there. What no recorded run has exercised is ADR 0163's
  own narrower residual — byte-stability across an add/remove on a profile that **already
  holds** slots, which is the state the captured profiles are in (`prof-A` carries three
  triples). Reordering or re-rendering on such a profile would make Guard B refuse. That
  residual belongs to #882.
- **The read rendering has not been shown to be valid `chsyscfg` input.** The capture reads;
  it never fed a read value back as a write. `none` in the pool position is documented
  nowhere on the input side, where the same position is written empty. #882 owns that round
  trip.

One string in `src/` is now stale and is #882's to fix, not this change's:
`PCIE_ASSIGNMENT_UNAVAILABLE_REASON`
(`src/hmc_mcp/operations/virtualization/pcie.py:43-46`) still reads "ADR 0053 admits no exact
dedicated PCIe profile readback; assignment cannot be safely verified". After this record the
first clause is false; what remains true is that no code path reads it. The operations keep
refusing either way, so nothing is unsafe in the interval — but the reason they give is wrong
until #882 rewrites it.

The record is pinned by sha256 in `tests/system/test_pcie_contract.py`, so editing published
evidence reddens a test instead of landing quietly, and the same pin asserts every probe's
exit status and the negative control's diagnostic — the fields the admission turns on.
Adding the record also moves the first-match `live-capture` selector in the RoCE test, which
now names its fixture: a third live capture would otherwise have silently changed which
record that test reads.

## Considered & rejected

- **Write a `documented`-support record citing an IBM `lssyscfg` page.** verified:
  `rg -c io_slots docs/refs/hmc-commands-p10/commands/lssyscfg.md docs/refs/hmc-commands-p11/commands/lssyscfg.md`
  exits 1 with no match in either file, and the IBM Power8 `lssyscfg` page
  (`https://www.ibm.com/docs/en/power8/8284-22A?topic=commands-lssyscfg`, fetched 2026-09-22)
  mentions neither `io_slots` nor a `-r prof` attribute list. The locator would have been
  fabricated.
- **Compose the `chsyscfg`/`mksyscfg` input grammar into a read admission.** verified: the
  corpus's own examples render an unset pool empty — `21030003//0` at
  `docs/refs/hmc-commands-p11/commands/mksyscfg.md:69` and `2105001B//0` at `:80` — where the
  capture's read path renders it `none`, so the two grammars demonstrably differ. ADR
  0053 (`:46-47`) and the state matrix's "do not compose" clause forbid it independently.
- **Admit the operator-held 2026-09-21 results document as corroborating evidence.**
  verified: `.gitignore:1-2` excludes `test-results*.json` because it holds raw API data
  with internal hostnames, IPs and serials, and the fixture schema requires a `source_url`
  starting `https://github.com/` (`tests/system/test_pcie_contract.py:183`). Nothing a
  reviewer can open, nothing to falsify.
- **Admit the single-field `lssyscfg -r prof -F io_slots` that this repository's live arm
  already issues.** verified: the capture contains no such probe — its only `io_slots` read
  is `-F lpar_name,name,io_slots --header` — and `parse_hmc_delimited_rows`
  (`src/hmc_mcp/ssh/commands.py`) requires the header row that form omits. Admitting it would
  repeat the invented-fixture failure recorded in
  `docs/solutions/2026-09-14-fixtures-invented-for-an-endpoint-never-spoken.md`.
- **Leave the hardware envelope unconfined and let #882 decide.** verified: the capture
  covers exactly one release/model pair; ADR 0053 (`:46-47`) records that a field admitted for one
  family cannot be assumed present in another, and ADR 0163 confined the live arm to this
  same pair for this same reason. Deciding by omission would have let #882 mutate arbitrary
  Power8, Power10 or Power11 profiles on one machine's readback.
- **Confine the envelope more tightly still, to VIOS partitions.** judgment: the limit is
  real and is recorded above, but a partition-role predicate is a runtime check in `src/`,
  which this change is excluded from; #882 is where it can be written and tested.
- **Open ADR 0055's gate in `src/` in the same change.** judgment: the split exists so the
  evidence question is reviewed before any code can mutate a real profile. Admitting the
  evidence and consuming it in one change gives that review nothing to stand on.
- **Do nothing and leave the sealed clause in place.** verified: the clause at
  `docs/adr/0053-evidence-backed-pcie-capability-contract.md::80-81` names a condition the
  published capture now meets, so leaving it would state a reason that no longer holds while
  #882 — which that same clause blocks — waits on it.
