# Wire dedicated PCIe assignment to the admitted `io_slots` readback — design

Issue #882 (part of #873). Decision record: [ADR 0166](../../adr/0166-dedicated-pcie-assignment-wiring-and-create-time-probe.md).
Governing records: ADR 0011 (ownership), ADR 0053 (evidence contract), ADR 0055 (fail-closed
profile contract), ADR 0163 (live arm), ADR 0165 (admitted readback and envelope).

## Problem

`assign_dedicated_pcie_slot`, `unassign_dedicated_pcie_slot` and create-time
`LparPcieAssignments.dedicated` authorize their target and then raise
`PcieAssignmentUnavailableError` unconditionally (`operations/virtualization/pcie.py`,
`operations/lpar/assignments.py:_analyze_assignment_requests`). ADR 0165 admitted the exact
readback `lssyscfg -r prof -m SYS -F lpar_name,name,io_slots --header` inside the envelope HMC
`V10R3 M1060` / model `8375-42A`, and left two preconditions on this change: enforce that
envelope before any mutating command, and establish that a value read back in the admitted
rendering (`drc/none/0`) is accepted as `chsyscfg` input — which no capture has shown.

## Scope

In: the three dedicated paths, the admitted read and its parser in `ssh/profiles.py`, the
envelope, the error surface, the ADR 0163 arm's two `ExpectedOutcome` SKIPs and create-time
probe, their tests, ADR 0166, and a CHANGELOG entry. Out: every exclusion frozen in `WORK:SCOPE`
(dynamic `chhwres -r io`, SR-IOV envelope scope, maturity promotion, the bare-cec arm, retiring
the arm's escape hatch, MCP/CLI signatures, facade promotion, any live HMC access).

### Behaviour

1. **Read.** `ssh/profiles.py:read_profile_io_slot_rows(config, system_name)` issues exactly
   `lssyscfg -r prof -m <system> -F lpar_name,name,io_slots --header` and returns
   `parse_hmc_delimited_rows` output. No `--filter`, no single-field and no headerless form.
2. **Parse.** `ssh/profiles.py:parse_profile_io_slots(value) -> tuple[ProfileIoSlot, ...]`.
   `none` (the whole value) parses to `()`. Otherwise split on `,`; each entry must be exactly
   three `/`-separated positions: a DRC index of exactly eight uppercase hexadecimal digits, a pool that is the literal `none`
   (→ `pool_id=None`) or a non-blank value kept verbatim, and `is_required` in `{0, 1}`. An empty
   value, an empty pool position, a wrong position count, a bad `is_required`, or a DRC index
   listed twice raises `HMCCLIError` naming the rendering as unadmitted. `ProfileIoSlot` is a
   frozen dataclass `(drc_index: str, pool_id: str | None, is_required: bool)`.
3. **Operation order**, both operations, each step before the next:
   (a) `require_command_safe_text` on `profile_name` (blank, `/`, `,`, `=`, `"`, control
   characters → `ValueError`) and `require_drc_index(drc_index)` — exactly eight uppercase
   hexadecimal digits, the form every captured and documented index takes, so identity is never
   a case-variant string — both before any HMC call;
   (b) `_authorize_pcie_profile_request` → `resolve_and_authorize_lpar_names` (ADR 0011; a foreign
   owner raises `PermissionError`);
   (c) envelope: `require_dedicated_pcie_environment(config, system_name)`, which shares the pure
   predicate `_is_admitted_environment(version, model)` with `require_admitted_environment`;
   outside it raise `PcieAssignmentUnavailableError` with the rewritten
   `PCIE_ASSIGNMENT_UNAVAILABLE_REASON`;
   (d) read the profile's row — exactly one row with `lpar_name` and `name` equal to the
   resolved LPAR and `profile_name`; zero rows → `ValueError`, more than one → `HMCCLIError`;
   (e) decide; (f) mutate with the existing `assign_profile_io_slot` / `unassign_profile_io_slot`
   (`io_slots±=<drc>//0`, no `--force`); (g) read back and verify.
4. **Decide.** The operation owns exactly one triple form, `WRITTEN = ProfileIoSlot(drc, None,
   False)` — what `<drc>//0` is expected to read back as. Assign: DRC absent → add; present as
   `WRITTEN` → return with no mutation (idempotent retry); present in any other form →
   `ValueError`, no mutation. Unassign: absent → return (idempotent); present as `WRITTEN` →
   remove; other form → `ValueError`, no mutation.
5. **Verify.** Expected after-state is the before-state plus (assign) or minus (unassign)
   `WRITTEN`, compared order-insensitively by DRC. The readback runs even when the builder
   raised, because a lost response may still have mutated. Readback equals the expected state →
   success. Builder raised and readback equals the before-state → re-raise the builder's error
   (nothing changed). Anything else, a readback failure included → the new
   `PcieAssignmentPartialError` (a `RuntimeError`) naming the cause and the before/after
   `io_slots` values, chained from the underlying exception.
6. **Create/modify time.** `_analyze_assignment_requests` validates dedicated selectors and
   rejects a duplicate `(profile_name, drc_index)` pair with `ValueError`; it no longer raises.
   `prevalidate_lpar_pcie_assignments` resolves the system name with `resolve_ssh_names` and
   calls `require_dedicated_pcie_environment(hmc.config, system_name)` when `dedicated` is
   non-empty, so an out-of-envelope create refuses before the partition
   exists. `apply_validated_lpar_pcie_assignments` catches `PcieAssignmentPartialError` beside
   the existing classes.
7. **Error surface.** `PcieAssignmentUnavailableError` is kept and now means only
   "outside the ADR 0165 envelope". The reason string becomes "dedicated PCIe profile assignment
   is admitted only for HMC V10R3 M1060 with managed-system model 8375-42A (ADR 0165)". Return
   types stay `None`.
8. **Live arm (ADR 0166).** Both `ExpectedOutcome` declarations and the reason import are removed.
   ST30's probe asks for create-time assignment with no expected refusal, records whether the
   probe profile lists the DRC, and is cleaned up **before** the fixture is created, so the slot
   sits in at most one profile at a time; the fixture is not created if that cleanup did not
   succeed, and `probe_created` stays set on failure so the final cleanup retries it once. Probe cleanup keeps its caller-token comparison and delete-by-UUID, and now decides
   slot removal on the probe's live `io_slots`: remove only if it lists the DRC, confirm it is
   gone, refuse the delete otherwise. ST31 assigns via `hmc_assign_dedicated_pcie_slot` only; the
   raw `io_slots+` is not issued after it. ST33/ST34 and every read keep the escape-hatch grammar.

## Failure model

- **Actors and deployments:** an MCP client or CLI operator holding HMC credentials, acting
  through the configured profile; the live arm run by an operator on admitted lab hardware; CI
  runs only unit tests. No anonymous or multi-tenant deployment.
- **Invariants and assets:** a real partition profile's `io_slots` list on the HMC; a slot on a
  foreign-owned LPAR (ADR 0011); no mutation outside `V10R3 M1060`/`8375-42A`; no `--force`; a
  read-rendered value is never written back as input. The decide step still assumes a stored
  `drc/none/0` is the element `<drc>//0` adds and removes (ADR 0166 decision 1); readback
  verification bounds that assumption.
- **Accepted failure classes:** (1) a verified mutation reported as `PcieAssignmentPartialError`
  when the HMC renders the written slot other than `drc/none/0`, or re-renders another slot —
  bounded: the profile holds a known change and the error carries both values, and after it
  both operations refuse that DRC, so recovery is a manual profile edit; (2) an empty profile
  whose admitted-form `io_slots` renders other than `none` — the whole-value `none` is inferred
  from the arm's unadmitted read, not the capture — fails closed: before mutation on assign, and
  as a partial error after unassigning a profile's last slot; create-time assignment on a fresh
  partition meets it first; (3) a concurrent writer between read and write — the readback
  mismatch reports it; no lock exists on the HMC CLI.
- **Covered elsewhere:** live acceptance of `<drc>//0` on the envelope and maturity — #879;
  non-VIOS exercise and the bare-cec consumer — #876; SR-IOV envelope widening — #667/#668;
  dynamic `-r io` — ADR 0053 matrix.

### Threat model

- **Boundaries:** caller strings `profile_name`/`drc_index` → `chsyscfg -i` record and shell
  (existing, now reachable); HMC `lssyscfg` output → parser (added).
- **Actors:** an authorized caller aiming at a partition it does not own; an HMC whose output
  deviates from the capture. Trust sits with the configured HMC credentials.
- **Controls:** `require_command_safe_text` plus `build_attribute_record` and `shlex.quote` on
  input; ADR 0011 authorization before any read; envelope before any read or write; strict
  parser that refuses any unadmitted rendering; readback verification after write.
- **Out of scope:** a compromised HMC; credential handling (unchanged).

## Success

1. With the envelope admitted and a readable profile, assign adds and unassign removes the DRC
   through the no-`--force` builders, and both verify by readback.
2. Out of envelope, both operations and create-time prevalidation raise
   `PcieAssignmentUnavailableError` with zero `chsyscfg` and zero `lssyscfg -r prof` calls.
3. The parser maps each captured `io_slots` value in `power9-v10r3m1060-live-ioslots.json` to
   triples with `pool_id=None`.
4. No code path in `src/` passes a parsed or read `io_slots` value to a `chsyscfg` builder.
5. The arm no longer declares a SKIP for either dedicated path and cleans the probe up before
   the fixture exists.

## Validation

`just test` (unit), `just verify`, `uv run --no-sync prek run --all-files`. Focused tests:
`tests/unit/test_pcie_assignment_contract.py` (order, envelope, idempotency, mismatch, foreign
owner, blank/unsafe selectors, parser incl. `none`), `tests/lpar/test_pcie_assignments.py`
(create-time envelope, duplicate, partial classification), `tests/system/test_pcie_contract.py`
(parser over the capture), `tests/scripts/test_pcie.py` (arm). Each guard is controlled-faulted.
Live verification is excluded from this change (orchestrator, operator authorization).
