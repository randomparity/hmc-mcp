# Exact `io_slots` profile readback admission

Issue: [#881](https://github.com/randomparity/hmc-mcp/issues/881).
Decision record: [ADR 0165](../../adr/0165-admitted-io-slots-profile-readback.md).

## Problem

Dedicated PCIe profile mutation is sealed by decision record, not by missing code.
ADR 0053's dedicated-slot profile clause — "Dedicated-slot profile grammar is recorded, but
profile mutation likewise remains capability-unavailable until exact `io_slots` readback is
admitted", at `:73-74` on `origin/main` and `:80-81` once this branch's Status banner lands —
makes profile mutation capability-unavailable. The issue cites the `:72-74` span, whose first
line belongs to the `chhwres -r io` sentence #873 owns. ADR 0055's two gate clauses
(`:24-25` and `:27-29` on `origin/main`, `:33-34` and `:37-38` after the banner) make every
assign and unassign fail closed on the same condition, and
`src/hmc_mcp/operations/virtualization/pcie.py:43-46` carries that condition as its error
text. Nothing downstream can be wired until the admission exists.

The admission could not be written from documentation, and that is the substance of the
problem rather than a formality. `io_slots` appears in the vendored corpus only as a
write-side input attribute — `rg -c io_slots docs/refs` reports hits in
`hmc-commands-p{10,11}/commands/chsyscfg.md` and `mksyscfg.md` and **no match** in either
`lssyscfg.md`, neither of which enumerates `-r prof` attributes at all. That corpus is
gitignored and operator-host-only, so it grounds only the negative; the IBM Power8
`lssyscfg` page — already an admitted read-side locator family via
`tests/fixtures/pcie/power8-profile.json` — was fetched on 2026-09-22, is openable by any
reviewer, and confirms the same negative independently. ADR 0053's no-cross-family rule
(`:39-40` on `origin/main`, `:46-47` after the banner) forbids admitting a field because
another family documents it, and the state matrix forbids composing mutation
evidence with read evidence, so a `documented`-support record here would be a fabricated
locator.

An operator capture published on 2026-09-22 settles it directly:
[#881 comment 5779662835](https://github.com/randomparity/hmc-mcp/issues/881#issuecomment-5779662835).

## Scope

One unit: admit the readback on that capture, record the decision, and move the state
matrix and its pins with it. **No runtime behaviour changes.** The operations keep failing
closed and `just verify` stays green with the gate shut.

- **New** `tests/fixtures/pcie/power9-v10r3m1060-live-ioslots.json` — a `live-capture`
  record (`support: "captured"`) on the `power9-v10r3m1060-live-sriov.json` precedent,
  carrying each probe's published command, fields, exit status, stdout and stderr verbatim,
  including the unknown-attribute negative control and the `lshmc -V` probe, whose complete
  236-byte stdout the comment publishes verbatim — `"version= ` wrapper, leading spaces, four
  iFix lines and trailing `base_version` record included. The precedent record's own
  `hmc-version` value is a tidied four-line form and is **not** the precedent for this
  field.
- **New** `docs/adr/0165-admitted-io-slots-profile-readback.md` — decides the admission, the
  corroboration question, the hardware envelope, and the supersession.
- **Changed** `tests/system/test_pcie_contract.py` — four moves. The new record forces three:
  `EXPECTED_FIXTURES`; the first-match `live-capture` selector in
  `test_captured_roce_rows_are_accepted_with_empty_ethc_companion`, which the new record's
  sort order would otherwise capture; and a new pin,
  `test_dedicated_profile_io_slots_capture_is_pinned`, asserting the record's identity, every
  probe's `exit_status` and `stderr`, the negative control's exact diagnostic, and the
  fixture's sha256. The fourth is the state-matrix row's own pins, in
  `test_operation_matrix_fails_closed_without_same_family_readback`, renamed to
  `test_operation_matrix_fails_closed_for_every_mutation_row` because the old name asserts a
  premise this change falsifies for the dedicated row.
- **Changed** `docs/workflow/specs/2026-08-20-pcie-capability-contract-design.md` — the
  "Assign/unassign dedicated slot" row's create-time and inactive cells state the new
  condition. The running cell is unchanged because `chhwres -r io` dynamic-grammar
  admission belongs to #873. The generalising sentence below the matrix ("A profile or
  effective operation is likewise unavailable unless its exact readback fields are admitted
  for the selected family") is **deliberately unchanged**: it states a necessary condition,
  not a sufficient one, so it stays true after this admission, and rewriting it would reach
  past criterion 3.
- **Changed** `docs/adr/0053-...md` and `docs/adr/0055-...md` — Status supersession banners
  only. Their bodies are append-only.
- **Changed** `docs/adr/0163-dedicated-pcie-live-evidence-arm.md` — Status block only,
  reconciled against ADR 0162:5-11.
- **Changed** `CHANGELOG.md`.

Ownership is a clean extension: the fixture directory already owns version-labelled
evidence records and `tests/system/test_pcie_contract.py` already owns their schema. No
responsibility moves, no caller migrates, and no path becomes obsolete.

Excluded, with owners: any `src/` change or builder selection (#882); SR-IOV/vNIC envelope
changes (out of #871); executing a new live run (operator); `chhwres -r io` dynamic-grammar
admission (#873); a REST-API readback path (unowned).

## Failure model

**Actors and deployments.** A repository reviewer reading the record and its
`source_url`; `just test` and CI's eight `ci` legs running the contract test;
`just secrets` (`git ls-files | detect-secrets-hook`), which reads the new fixture and the
new sha256 literal once staged; `just adr-numbering`, which reads ADR filenames and H1s;
`just doc-freshness`, which reads every tracked Markdown file's first line; the #882
implementer reading ADR 0165 as the authority for what may be wired; and
`scripts/live_test/pcie.py:profile_io_slots_command`, the one in-tree reader of this value
format, which today builds a `--filter`-narrowed single-field read without `--header` —
a form this capture does not admit. No runtime actor: no `src/` code reads this record, and
none is added here.

**Invariants and assets at stake.**

- Evidence honesty: every claim in the record traces to the published capture.
- Redaction: the record is public and must carry no lab identifier the capture did not
  already publish in redacted form.
- The closed gate: `just verify` stays green with both operations still failing closed.
- The envelope decision binds #882; an omission there is a silent authorization.
- Tamper evidence: the fixture's sha256 is pinned, so an edit to published evidence is loud.

**Accepted failure classes.**

- Byte-stability across an add/remove **on a profile that already holds slots** is
  unanswered. The 2026-09-21 run ADR 0162 records did exercise a round trip on a profile that
  run created, and its teardown restored a byte-identical baseline; the already-populated
  case — the state the captured profiles are in — is the residual ADR 0163 itself names.
  Bounded: nothing in this change reads or compares the value. Owned below.
- Non-VIOS dedicated-slot assignment is unexercised — the captured system carries two
  profiles, both on the VIOS partition. Bounded: this change admits a readback only, and
  the record and ADR state the limit.
- Probe `stdout` fidelity for the four identifier-bearing probes cannot be checked against
  the comment's published byte counts, because those counts are pre-redaction (50/73/166/126)
  and the transcripts are post-redaction (48/64/157/126); fidelity there rests on the capture
  author. Bounded: the two probes that print no identifier — `lshmc -V` and the
  unknown-attribute control — are independently byte-checkable and match their published
  figures exactly at 236 and 126; every probe's exit status and stream is published, so none
  is inferred.
- Release and model outside `V10R3 M1060` / `8375-42A` are not admitted at all. Bounded by
  the envelope decision, which is the confinement rather than a gap in it.

**Covered elsewhere.**

- Wiring the readback into `src/`, selecting a builder, and enforcing the envelope in code —
  issue #882.
- `chhwres -r io` dynamic-grammar admission — issue #873's state-matrix row.
- The live arm's Guard B behaviour under a non-byte-stable `io_slots` — ADR 0163.
- `PCIE_ASSIGNMENT_UNAVAILABLE_REASON`
  (`src/hmc_mcp/operations/virtualization/pcie.py:43-46`) still reads "ADR 0053 admits no
  exact dedicated PCIe profile readback", which ADR 0165 falsifies. `src/` is excluded from
  this run, so the string is a deferral owned by #882, not a defect left unowned.
- `scripts/live_test/pcie.py:profile_io_slots_command` builds a form ADR 0165 does not admit
  and its docstring cites ADR 0053 as admitting it — `scripts/` is outside this run's
  surface; recorded as a follow-up for #882's owner.

## Threat model

**Boundary inventory.** One boundary, widened not added: this change publishes one more
redacted HMC transcript into a public repository. It adds no input parser, no entry point,
and no command construction.

**Actor model.** The reader of a public repository is untrusted with respect to lab
identifiers. The redaction owner is the party the capture comment names — the campaign
orchestrator, acting on operator instruction with operator-provided lab access — and this
change trusts the published comment and nothing behind it.

**Control per boundary.** The record's probe `stdout` values are copied verbatim from the
already-redacted published comment, so the redaction decision is the capture author's and is
not re-made here. `just secrets` (detect-secrets against `.secrets.baseline`) reads every
tracked file, including the new fixture. The sha256 pin makes a later edit to that fixture
fail a test rather than land quietly.

**Explicitly out of scope.** Whether the published redaction is itself sufficient — the
capture author owns that, and this change is forbidden from obtaining, reconstructing, or
guessing the unredacted values. Secrets scanning of the ignored operator-held results
document, which this change does not read.

## Success

1. `tests/fixtures/pcie/power9-v10r3m1060-live-ioslots.json` exists, satisfies the
   live-capture schema pinned by
   `tests/system/test_pcie_contract.py::test_evidence_records_have_closed_versioned_shapes`,
   and cites the
   capture comment as `source_url`.
2. ADR 0165 decides all four questions the issue names, states the three limits it carries
   (VIOS-only; byte-stability on an already-populated profile; the read rendering's
   unexercised validity as `chsyscfg` input), and
   carries a `Considered & rejected` list whose factual grounds are tagged `verified:` with
   the command and result behind them.
3. ADR 0053, ADR 0055, and ADR 0163 stop contradicting this change and each other: 0053 and
   0055 carry supersession banners, and 0163's Status records that the 2026-09-21 run
   happened and points at ADR 0162 for it. It restates no PASS/SKIP/FAIL matrix — AGENTS.md
   makes a live matrix evidence only for the commit it ran on, and that commit is recorded
   only in the gitignored results document. Falsifiable by reading 0163's Status for the
   absence of a matrix and the presence of the ADR 0162 pointer.
4. The state-matrix row and every test pin that reads it move together.
5. `just verify` and `uv run --no-sync prek run --all-files` both exit 0 with no `src/`
   change on the branch.

## Validation

- **Contract: the new record satisfies the live-capture schema.**
  Mode: `focused-test`. `tests/system/test_pcie_contract.py::test_evidence_records_have_closed_versioned_shapes`
  reds on the `EXPECTED_FIXTURES` equality assertion before the set is updated.
  Green: `uv run --no-sync pytest tests/system/test_pcie_contract.py -k closed_versioned --no-cov -q`.
- **Contract: the RoCE test still reads the SR-IOV capture, not the new one.**
  Mode: `focused-test`. `test_captured_roce_rows_are_accepted_with_empty_ethc_companion`
  reds with `StopIteration` on its `physical-ports` probe lookup once the new record sorts
  ahead of the SR-IOV one; green after the selector names its fixture.
  Green: `uv run --no-sync pytest tests/system/test_pcie_contract.py -k roce --no-cov -q`.
- **Contract: the new record's identity, probe statuses and bytes are pinned.**
  Mode: `focused-test`. A new `test_dedicated_profile_io_slots_capture_is_pinned` asserts the
  ordered probe names, the `io_slots` probe's exact command and fields, every probe's
  `exit_status` and `stderr`, the negative control's exact diagnostic string, and the
  fixture's sha256; it reds with `KeyError` before the fixture exists.
  Green: `uv run --no-sync pytest tests/system/test_pcie_contract.py -k io_slots_capture --no-cov -q`.
- **Contract: the state-matrix row and its pins agree.**
  Mode: `focused-test`. `test_operation_matrix_fails_closed_for_every_mutation_row` (renamed
  from `..._without_same_family_readback`) reds when the row changes without its pin.
  Green: `uv run --no-sync pytest tests/system/test_pcie_contract.py -k matrix --no-cov -q`.
- **Contract: the new sha256 literal does not redden the secrets gate.**
  Mode: `focused-test`. `just secrets` reds on a bare hex high-entropy literal; green once it
  carries `# pragma: allowlist secret`, matching the existing pin on
  `power9-v10r3m1060-live-sriov.json`.
  Green: `just secrets`.
- **Contract: ADR 0165's filename and H1 agree.**
  Mode: `focused-test`. `just adr-numbering` reds on a mismatched H1. Green: `just adr-numbering`.
- **Contract: the ADR Status banners and prose.**
  Mode: `task-test-not-applicable`. `just adr-numbering` checks an ADR's filename and H1 and
  never its body (AGENTS.md, *Repository conventions*), and no executable consumer parses an
  ADR Status banner, so no task-specific observation could fail meaningfully. Reviewed by
  reading.
- **Contract: no runtime change.**
  Mode: `focused-test`. `git --no-pager diff --name-only $(git merge-base HEAD origin/main) -- src/`
  must print nothing, and `just verify` must exit 0 with both operations still failing closed
  (`tests/` already pins those refusals).
