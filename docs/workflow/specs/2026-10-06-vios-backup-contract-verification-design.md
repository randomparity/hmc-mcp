# VIOS backup and install contract verification (V9a, #1349)

Part of #633, epic #620. Pattern: #627 / PR #1320.

## Problem

Five VIOS operations have no maturity record, and their `operations.json` rows are a
bulk assignment that names neither the command each handler issues nor, for
`lpar.install_os`, the right command family (it is bound to migration and
remote-restart rows). The `lsviosbk -F name,type` projection has only ever been
captured empty, so its attribute names are unverified.

## Scope

1. **Row binding.** Each operation names exactly the command its handler issues:

   | Operation | Rows |
   |---|---|
   | `vios.list_backups` | `cli:commands/lsviosbk` |
   | `vios.backup` | `cli:commands/mkviosbk` |
   | `vios.restore` | `cli:commands/rstviosbk` |
   | `vios.install` | `cli:commands/installios` |
   | `lpar.install_os` | `cli:commands/installios` |

   Selector resolution (REST VIOS/LPAR reads) is shared plumbing and is not bound, as
   in PR #1320.

2. **`vios-backup` live arm** (subtask 37, scenario `st37-vios-io-backup-restore`),
   dispatched only by `scripts/live_vios_backup.py` (`--group vios-backup`); the
   subtask SKIPs under any other group, as ST10's VIOS round trip does. Steps:
   1. *Preconditions* (non-promoting rows; any failure SKIPs every later step):
      the test partition (`LIVE_TEST_LPAR_NAME`) is `Not Activated`; it is the only
      non-VIOS partition on `LIVE_TEST_SYSTEM_NAME`; exactly one VIOS holds disk
      (`VirtualDisk`/`PhysicalVolume`) mappings toward it, and exactly one such
      mapping. The VIOS's management-interface-vs-SEA placement is recorded, not gated
      (the operator rules on it before the run; runbook step).
   2. *Baseline*: the VIOS's full REST mapping list, and via `viosvrcmd` the text of
      `lsmap -all`, `lsmap -all -net`, `lsmap -all -npiv`, `lsdev -virtual`.
      The mapping identity (`vhostN/<vtd>`, backing name) and the backup name go into
      `LiveTestArtifacts` before any mutation, so the recovery check can read them.
   3. *Read*: `hmc_list_vios_backups` before the backup (non-promoting row); the run's
      backup name must be absent.
   4. *Backup*: `hmc_backup_vios(-t viosioconfig, backup_name=hmcpctl-live-st37-<8 hex>)`;
      a raw `lsviosbk -F --header` capture row (non-promoting) before and after, for the
      attribute names; the listing after the backup is the `vios.list_backups`
      observation.
   5. *Delta*, only when the backup call passed and the raw after-capture names the
      backup (the parsed listing is asserted, not gated on: its projection is unverified):
      `viosvrcmd ... rmvdev -vtd <vtd>`; assert the disk mapping is gone and the server
      adapter remains (`lsmap -vadapter vhostN` succeeds).
   6. *Restore*: `hmc_restore_vios(-t viosioconfig, restart_if_required=True)`, timed.
      With `-r` the HMC restarts the VIOS and retries inside the command, so the run
      sets `HMC_SSH_TIMEOUT=2400` (runbook, preflight). Whatever the call returns,
      the arm then waits, bounded at 30 min (poll 30 s), until `viosvrcmd ... ioslevel`
      answers. If it never answers, or the call ended without an HMC exit status
      (a timeout or dropped session; the HMC may still be restoring), the outcome is unknown: assertions are still read, but no further
      mutation runs, and recovery names both remedies. The arm itself refuses to
      start below that timeout.
   7. *Assert*: `mapping-restored` — the mapping list has the baseline mapping id with
      the same backing; `baseline-restored` — the VIOS's REST mapping set, compared
      order-insensitively as (id, lpar_uuid, backing_kind, backing_name), and each of the
      four texts, compared as its set of whitespace-normalized non-empty lines, equal the
      baseline's. Any difference is recorded with its diff.
   8. *Fallback*: when step 7 read the mapping list and it lacks the mapping,
      `mkvdev -vdev <backing> -vadapter vhostN -dev <vtd>`, then re-read; the restore
      observation stays failed.
   9. *Cleanup*: when the final read shows the mapping back (restored or recreated),
      `rmviosbk -t viosioconfig -m <sys> -p <vios> -f <name>` through
      `hmc_run_command`, then confirm absence (parsed listing, else the raw capture).
      Otherwise the backup is kept for the operator and recovery reports it.

   A read that fails after a mutation counts as an assertion not holding and permits
   no further mutation; it never reads as "absent".

   Observations (`record_verified`, ADR 0126):

   | Operation | Assertions | Cleanup |
   |---|---|---|
   | `vios.list_backups` | `listing-parsed`, `listing-names-run-backup` (parsed row with the run's name, type `viosioconfig`) | not-required |
   | `vios.backup` | `backup-accepted`, `backup-newly-listed`, `backup-type-viosioconfig` | rmviosbk confirmed absent → passed; kept (step 9) → not-run; else failed |
   | `vios.restore` | `restore-accepted`, `mapping-restored`, `baseline-restored` | final mapping equals baseline → passed, else failed |

   A tool-call failure records the assertion as not holding; it never becomes a SKIP.
   The restore's outcome is judged on steps 6–7, never on the call alone.

3. **Preflight** names the arm's mutations; **recovery** witnesses subtask 37: a
   listed backup with the run's name (remedy: the `rmviosbk` command), and a missing
   baseline mapping (remedy: the `mkvdev` command). A failed read
   raises `StateUnreadable` (exit 2) as the existing witnesses do. It adds only
   `hmc_list_vios_backups` to its read-only allowlist.
4. **Catalog**: maturity records for the five operations (below), regenerated
   projection and `docs/tools/`.
5. **Install reconciliation (offline).** `vios.install` is implemented as a detached
   `installios` submission; a submit is not an outcome, so it has no promoting path
   here. `lpar.install_os` resolves its target only through the `LogicalPartition`
   feed, which never lists a VIOS (#1202), refuses a VIOS name, then requires
   `PartitionType == "Virtual IO Server"`. No target can pass both, so it never
   submits — a confirmed defect whose fix (`operations/vios/install.py`, a contract
   change) is outside this surface and is routed to the orchestrator for an owning
   issue. It stays bound to `installios`, the command its code path builds, and is
   recorded `absent`, missing `vios-install-via-lpar-selector`.
6. **Defects** confirmed within the permitted surface (for example the `lsviosbk`
   projection, if the live capture disproves it) are fixed here and the run repeated
   at the fixed head.

### Live gaps

| Case | Prerequisite |
|---|---|
| `vios.install` / `lpar.install_os` positive | a disposable VIOS-type partition, install media, a NIM network, a maintenance window |
| `vios.backup -t vios`, `-t ssp`; `vios.restore -t ssp` | full-image: backup storage and window; SSP: a cluster-aware VIOS (none in the boundary) |
| backup removal tool | #698 |

## Failure model

1. **Actors and deployments** — the operator running the arm from a lab host against
   the V10R3 / POWER9 mutation-boundary system; CI never runs it.
2. **Invariants and assets at stake** — the VIOS I/O configuration (mappings, SEA);
   the test partition's disk mapping; no other partition's I/O; catalog truth (no
   promotion without asserted postconditions).
3. **Accepted failure classes** — a VIOS restart during restore (operator-authorized,
   `-r`); a restore outage of the boundary VIOS (no other clients, precondition-gated);
   line order and whitespace in the four VIOS listings (normalized away; any other
   difference fails `baseline-restored`).
4. **Covered elsewhere** — backup-removal tool: #698; access policy and ownership
   guards: existing runtime guards; recovery-check read-only enforcement:
   `guard_read_only`.

### Threat model

- Boundaries: harness-built `viosvrcmd`/`rmviosbk` strings from HMC-returned names;
  recovery remedies built from a results document.
- Actor: the local operator; the HMC is trusted for names it returns.
- Controls: `shlex.quote` on every interpolated name; the backup name is generated
  by the arm and passes `validate_vios_backup_name`; recovery prints, never runs,
  its remedies and refuses shell metacharacters as today.
- Out of scope: a hostile HMC.

## Success

- The five rows bind exactly as tabled; `just capability-inventory` passes.
- Five maturity records; the three live ones carry the run's observations, failed
  where an assertion failed.
- Unit tests pin each arm step's decision, the precondition SKIP, the fallback, the
  cleanup disposition, preflight's verdict and recovery's two findings.
- `just verify` and `prek run --all-files` pass.

## Validation

Focused tests: `tests/test_live_vios_backup_arm.py` (arm steps against a scripted
fake `state.call`), `tests/scripts/test_live_vios_backup.py` (wrapper),
`tests/scripts/test_live_test_preflight.py`, `tests/scripts/test_live_test_recovery.py`,
`tests/test_live_runner.py` (group table, artifact round trip); the catalog through
`just capability-inventory`; the runbook through `tests/test_live_testing_doc.py`.
The live run itself is the proof of the arm against hardware.
