# PCIe, SR-IOV and vNIC evidence without widening admission (V6, #630)

Part of #620 (child epic #1243). Pattern: #627 / PR #1320, #629.

## Problem

Thirteen PCIe, SR-IOV and vNIC operations are admitted. Their `operations.json` rows are a
bulk assignment that names SR-IOV statistics-clearing and adapter-mode jobs. No handler
issues those jobs. Nine of the thirteen have no maturity record. The four that have one rest
on the bare-cec arm's dedicated-slot observations and on an `sriov.set_mode` record with no
evidence. The SR-IOV and vNIC reads are exercised live only as plain `record` rows, and plain
rows never promote. The ADRs that admitted these operations (0053–0058, 0113, 0163, 0165, 0166,
0183) record evidence states that later changes made stale.

## Scope

1. **Row binding.** Each operation names exactly the CLI commands its handler issues.
   Shared plumbing is not bound, as in PR #1320. Shared plumbing here means selector
   resolution and the envelope reads, `lshmc -V` and `lssyscfg -r sys -F type_model`.

   | Operation | Rows |
   |---|---|
   | `pcie.list_dedicated_slots`, `io_slot.list` | `cli:commands/lshwres` |
   | `pcie.list_sriov_adapters`, `pcie.list_sriov_physical_ports`, `pcie.list_sriov_logical_ports` | `cli:commands/lshwres` |
   | `sriov.set_mode` | `cli:commands/lshwres` (it only reads) |
   | `vnic.list` | `cli:commands/lshwres` |
   | `pcie.assign_dedicated_slot`, `pcie.unassign_dedicated_slot` | `cli:commands/chsyscfg`, `cli:commands/lssyscfg` (profile write, LPAR-state and `io_slots` readback) |
   | `sriov.assign_logical_port` | `cli:commands/chhwres`, `cli:commands/lshwres`, `cli:commands/lssyscfg` |
   | `sriov.unassign_logical_port` | `cli:commands/chsyscfg`, `cli:commands/lssyscfg` |
   | `vnic.add`, `vnic.remove` | `cli:commands/chhwres`, `cli:commands/lshwres`, `cli:commands/lssyscfg` |

   `io_slot.list` loses its `composite_reason`: it issues one command.

2. **Read-only inventory phase in the dedicated arm.** This is subtask 24, scenario
   `st29-pcie-inventory`. It runs after ST29's environment admission and dedicated-slot
   listing, before slot selection, and before anything is created. It issues reads only.
   A failed read records its assertion as not holding. It never SKIPs the arm, and it never
   changes what the arm selects or mutates. An empty listing proves no row shape. It records a
   non-promoting `<tool> (empty)` row and a gap, never an observation. Every observation uses
   `record_verified` with cleanup `not-required`.

   | Operation | Call | Assertions |
   |---|---|---|
   | `pcie.list_dedicated_slots` | the existing ST29 baseline listing | `capability-available`, `slot-rows-identified` (≥ 1 item, each with a non-blank, unique `drc_index`), `owners-normalized` (no `owner_lpar` is the literal `null`) |
   | `io_slot.list` | `hmc_list_io_slots` (`all`, then `eth`) | `slot-rows-identified` (≥ 1 row, each with a `drc_index`), `matches-dedicated-inventory` (the `drc_index` set equals the dedicated listing's), `class-filter-narrows` (`eth` rows are a subset, each `pci_class` `0200`) |
   | `pcie.list_sriov_adapters` | unfiltered, then `adapter_id=<A>` | `capability-available`, `adapter-rows-parsed` (each `mode` is `sriov` or `dedicated`, and each `dedicated` row has a null `adapter_id`), `adapter-filter-selects-one` (exactly adapter A) |
   | `pcie.list_sriov_physical_ports` | `adapter_id=<A>`, then without one | `capability-available`, `ports-belong-to-adapter`, `port-state-normalized` (`up` or `down`), `granularity-positive` (each minimum granularity a decimal > 0), `adapter-required-refused` (the call without `adapter_id` fails) |
   | `pcie.list_sriov_logical_ports` | `adapter_id=<A>` | `capability-available`, `ports-belong-to-adapter`, `parents-are-listed-ports` (each `physical_port_id` is a port the previous read listed), `configured-capacity-bounded` (each configured port has 0 < capacity ≤ maximum ≤ 100, and each `unconfigured` port has no capacity) |
   | `vnic.list` | `hmc_list_vnics` on partition V | `vnic-rows-parsed` (≥ 1 row, each naming partition V and a `slot_num`) |

   Adapter A is the first adapter in the unfiltered listing whose `mode` is `sriov`. When no
   adapter qualifies, the two port reads record a SKIP row and a gap. Partition V is the first
   `lpar_name` that a read-only `lshwres -r virtualio --rsubtype vnic --level lpar -m <system>
   -F lpar_name` returns through `hmc_run_command`. When that read returns the empty-result
   line, `vnic.list` records the empty row and a gap.

   `sriov.set_mode` stays out of the phase unless the orchestrator grants it (see
   Ambiguities). If granted, it adds `current-mode-confirmed` (a call with adapter A's current
   mode returns the "already in" answer), `other-mode-refused` (the other mode fails) and
   `adapter-mode-unchanged` (re-read adapter A's mode equals the first read). The observation's
   variant is `current-mode-confirmation`. It is never recorded as a transition.

3. **One live observation per operation.** The catalog keeps one
   (`docs/capabilities/README.md`). When a run yields several for one operation, the record
   takes a failed observation if any failed. Otherwise it takes the arm's primary scenario.
   For `pcie.assign_dedicated_slot` that is ST31 (`st29-dedicated-pcie`) ahead of ST36
   (`st36-io-slots`). `pcie.unassign_dedicated_slot` is observed only by ST36, because ST33's
   unassign uses the raw grammar (ADR 0166 decision 4). When ST36 SKIPs, the existing ST35
   bare-cec observation stays in the catalog unchanged. Observations for operations outside
   this issue (`lpar.create`, `lpar.delete`, `command.run`) are not copied into the catalog.

4. **Maturity records** for all thirteen operations. The runtime projection and `docs/tools/`
   are regenerated.

   | Operation | Implementation | Missing scope |
   |---|---|---|
   | `io_slot.list` | `all-classes`, `pci-class-filter` | — |
   | `pcie.list_sriov_adapters` | `admitted-read-envelope` | — |
   | `pcie.list_sriov_physical_ports`, `pcie.list_sriov_logical_ports` | `adapter-scoped` | — |
   | `sriov.set_mode` | `current-mode-confirmation` (unchanged) | `adapter-mode-transition` (unchanged; #667) |
   | `sriov.assign_logical_port` | `dynamic-assign` (Not Activated, or Running with active RMC) | — |
   | `sriov.unassign_logical_port` | `not-activated-profile-unassign` | `running-dynamic-unassign` (ADR 0056: no capture) |
   | `vnic.list` | `lpar-scoped` | — |
   | `vnic.add` | `single-sriov-backing` | `failover-backing` (ADR 0057; #669) |
   | `vnic.remove` | `by-slot` | — |

   The dedicated-slot records keep their scopes.

5. **ADR reconciliation.** Each record's recorded evidence was checked against the code at
   this branch. Where a Status statement is no longer true, a dated evidence note is added to
   its Status. No decision text changes. Findings are tabled under *ADR reconciliation*.

6. **Docs.** `docs/live-testing.md` (dedicated-arm section) names the read phase, its
   scenario, and the snapshot the operator takes. Preflight's dedicated disclosure gains
   the read phase as reads, with no new mutation.

## ADR reconciliation

| ADR | Claim checked | Code at branch | Action |
|---|---|---|---|
| 0053 | Identities, decimal percentages, no `--force`, `chhwres -r io` sealed | Holds (`ssh/sriov.py`, `ssh/profiles.py`; no `chhwres -r io` in `src/`) | none |
| 0054 | "Until a version-labelled fixture admits an exact read projection, SR-IOV operations return capability unavailable without issuing a command" | The condition was met by ADR 0056 and widened by ADR 0183. The reads now populate on admitted pairs. | Status evidence note |
| 0055 | Status: "Issue #882 owns that selection and the envelope check" | #882 landed as ADR 0166 | Status evidence note |
| 0056 | Read levels, mutation cells, set-mode reads only, no `--force` | Holds. Physical-port levels per 0113/0183. Running assign requires active RMC (`_require_sriov_assignment_capacity_and_state`). Unassign is Not Activated profile-only. | none |
| 0057 | `-p` add grammar, `-p … -s` remove, ensure-one, ADR 0056 envelope | Holds (`ssh/vnic.py`, `operations/virtualization/vnic.py`). No harness has exercised add/remove. | none (gap recorded in catalog) |
| 0058 | Declarative assignment | Excluded (#626) | none |
| 0113 | Adapter-ID validation, `1`/`0` → `up`/`down` | Holds (`validate_adapter_id`, `list_sriov_physical_ports`) | none |
| 0163 | Arm design; Status already corrected via 0165/0166 | Holds | none |
| 0165 | Admitted readback form | The arm and the operations issue exactly that form (`profile_io_slot_rows_command`). The arm no longer issues the `--filter` single-field read this record names as unadmitted. | evidence note in 0166, which carries the claim |
| 0166 | Status: "No live run has exercised this change". Consequences: the arm's own read "stays the `--filter` single-field form"; maturity "stays `unrecorded`" until the live window exercises assign, unassign and an `is_required=1` element | Bare-cec ST35, the dedicated arm ST31 and #912's ST36 have exercised all three. ST36 removes an `is_required=1` element with the raw `//0` grammar. The read is the admitted form. | Status evidence note, updated from this run |
| 0183 | Per-pair read envelope; mutations keep the 0056 envelope | Holds (`_SRIOV_READ_ENVELOPE`, `require_admitted_environment`) | none |

## Live procedure

Run the procedure on the V10R3 / POWER9 mutation-boundary system, at this branch's final
head. Follow `docs/live-testing.md`: preflight `--group dedicated`, then
`scripts/live_dedicated.py`, then `scripts/live_test_recovery.py --results
test-results-dedicated.json`. Before and after the run, the operator saves three read-only
snapshots outside the repository: I/O slot ownership (`lshwres -r io --rsubtype slot -F
drc_index,lpar_name`), the partition list (`lssyscfg -r lpar -F name,state`), and the SR-IOV
adapter and logical-port inventory. After recovery, all three must match. The run's only
mutations are the existing dedicated-arm ones: a run-stamped scratch partition, plus
assignment of slots that no partition owns and no profile lists.

## Live gaps

| Case | Prerequisite |
|---|---|
| `sriov.assign_logical_port`, `sriov.unassign_logical_port` live | an SR-IOV arm run authorized for this window (the arm exists, subtask 23; excluded here) |
| `vnic.add`, `vnic.remove` live | an authorized vNIC round trip on a disposable partition; no harness step exists |
| `sriov.unassign_logical_port` on a Running partition | a capture of a successful dynamic removal of an unclaimed port (ADR 0056) |
| `sriov.set_mode` transition | excluded (#667); the operation refuses transitions |
| `vnic.add` failover backing | excluded (#669) |
| a non-empty `vnic.list` | a partition with a vNIC at run time |
| SR-IOV port reads | an adapter in SR-IOV mode at run time |
| `sriov.set_mode` current-mode confirmation | orchestrator grant (Ambiguities), else none |

## Failure model

1. **Actors and deployments.** The operator runs the dedicated arm from a lab host against the
   V10R3 / POWER9 mutation-boundary system. CI never runs it.
2. **Invariants and assets at stake.** Slots owned by the VIOS, the test partition or any
   other partition are never moved. SR-IOV adapter modes, ports, logical ports and vNIC
   backings are never changed by this issue's additions. Catalog truth holds: there is no
   promotion without asserted postconditions, and a failure is never relabelled.
3. **Accepted failure classes.** The read phase can fail on an HMC answer it did not expect.
   That is recorded as a failed observation, not a SKIP. The dedicated arm's existing
   failure classes are unchanged by this issue.
4. **Covered elsewhere.** Ownership and access policy are covered by the existing runtime
   guards. Adapter-mode, port, vNIC-backing and HEA mutations are #667–#670. Declarative
   orchestration is #626.

### Threat model

- Boundaries. The read phase adds one harness-built `hmc_run_command` read. The command is
  built from the configured system name with `shlex.quote`.
- Controls. The read phase issues no mutating tool unless the `set_mode` grant is given, and
  that tool only reads. Admission code is unchanged: `git diff` over
  `src/hmcpctl/operations/` and `src/hmcpctl/ssh/` is empty.
- Out of scope: a hostile HMC.

## Success

- The rows bind as tabled, and `just capability-inventory` passes.
- There are thirteen maturity records. Live ones carry this run's observations, failed where
  an assertion failed.
- Unit tests pin each read-phase assertion and its negative, the empty-listing path, the
  no-SR-IOV-adapter path, and the read phase's independence from slot selection.
- `just verify` and `prek run --all-files` pass.

## Validation

- `tests/scripts/test_pcie.py` covers the read phase against the scripted `ScenarioState`.
- The catalog is checked through `just capability-inventory`.
- The live run is the proof against hardware.

## Ambiguities

Both rulings are referred to the orchestrator: where the read phase lives, and whether
`sriov.set_mode`'s read-only confirmation may run live. The defaults are the narrower
choices: no new subtask, and `set_mode` stays a gap.
