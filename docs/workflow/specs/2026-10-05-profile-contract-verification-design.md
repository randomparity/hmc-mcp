# Verify existing profile, memory-pool and affinity contracts (V3)

Issue #627 (epic #620, entry V3). Decision record: [ADR 0201](../../adr/0201-profile-sync-is-a-partition-setting.md).

## Problem

Twenty-one registered operations in this slice have no maturity record. Their `row_ids` in
`docs/capabilities/operations.json` name commands they never issue: the configuration reads
point at `chlparstate`/`migrlpar` rows and the profile tools at REST job rows. The live harness
calls most of them, but only through `state.record`, which never promotes. Live captures taken
on 2026-10-05 before this design found:

- **Sync.** `chsyscfg … sync_curr_profile=1` sets a persistent partition setting (ADR 0201).
  On a not-activated partition it wrote no profile. ST10 and ST15 leave it enabled.
- **Processor compatibility.** ST10 skips the set when the profile mode is `default`, on the
  grounds that chsyscfg rejects it. The reference lists `default` as valid, and an idempotent
  live set of `default` exited 0.
- **Backup path.** ST10 backs up to a local temp-directory path. `bkprofdata -f` names an HMC
  path; a relative name lands in `/var/hsc/profiles/<serial>/`.

## Operations in scope

| Operation | Command(s) issued | Rows bound | Implemented variant |
|---|---|---|---|
| `lpar_profile.backup` | `bkprofdata` | `cli:commands/bkprofdata` | `hmc-path-file` |
| `lpar_profile.restore` | `rstprofdata` | `cli:commands/rstprofdata` | `full-restore`, `merge-backup-wins`, `merge-current-wins` |
| `lpar_profile.sync` | `lssyscfg`, `chsyscfg` | `cli:commands/lssyscfg`, `cli:commands/chsyscfg` | `sync-setting-mode` |
| `lpar.set_description` | `lssyscfg`, `chsyscfg` | same | `ownership-preserving-text` |
| `lpar.set_msp` | `lssyscfg`, `chsyscfg` | same | `vios-flag` |
| `lpar.set_proc_compat` | `lssyscfg`, `chsyscfg` | same | `profile-mode` |
| `lpar.get_description`, `lpar.get_msp`, `lpar.get_proc_compat`, `lpar.get_minimum_affinity_policy` | `lssyscfg` | `cli:commands/lssyscfg` | `by-name-or-uuid` |
| `system.get_proc_compat_modes` | `lssyscfg` | `cli:commands/lssyscfg` | `system-scoped` |
| `memory_pool.list` | `lshwres` | `cli:commands/lshwres` | `system-scoped` |
| `memory_pool.remove` | `lshwres`, `chhwres` | `cli:commands/lshwres`, `cli:commands/chhwres` | `unassigned-pool-precheck` (partial; see Live gaps) |
| `lpar.get_memopt_score`, `lpar.list_memopt_scores`, `system.get_memopt_score` | `lsmemopt` | `cli:commands/lsmemopt` | `current-score` |
| `lpar.plan_memopt_scores`, `system.plan_memopt_score` | `lsmemopt` | `cli:commands/lsmemopt` | `calculated-score` |
| `resource_group.list_memopt_scores`, `resource_group.plan_memopt_scores` | `lshmc`, `lsmemopt` | `cli:commands/lshmc`, `cli:commands/lsmemopt` | `capability-gated` |
| `snapshot.assess_affinity` | none (local) | none; `composite_reason` says so | `local-snapshot` |

`lpar_profile.sync` and `lpar.set_description` read `lssyscfg` for the ownership guard.
`set_minimum_affinity_policy` and `migrate_affinity` are excluded (#626, #631).

## Design

1. **Sync mode (ADR 0201).** `ssh.profiles.sync_lpar_profile`,
   `operations.lpar.configuration.synchronize_lpar_profile` and `hmc_sync_lpar_profile` take
   `mode` (`enable`, `disable` or `suspend`; default `enable`), rendered `1`, `0` or `2`. Any
   other value raises `ValueError` before I/O. The docstrings describe the setting, and the
   enable mode keeps a warning that on an active partition synchronization can overwrite the
   active profile.
2. **Arm `profiles`.** The runner group is `[0, 4, 10, 15]`, dispatched by the wrapper
   `scripts/live_profiles.py` (tested by `tests/scripts/test_live_profiles.py`), with
   preflight and recovery as for every arm. The runner records the dispatched group in
   `RunState.group`.
3. **ST0** also captures the test partition's `sync_curr_profile` and `state` through one
   `hmc_run_command` read.
4. **ST4** records each read through `record_verified`, with assertions taken from the captured
   shapes:
   - the description is a string;
   - MSP is a bool;
   - the mode list contains `default` and the current mode;
   - the profile mode is in that list;
   - each score is an integer from 0 to 100 for the named partition;
   - plans carry `prediction_guaranteed` false.

   Assertion ids name the branch taken. For the gated reads they are `capability-available-rows`
   or `capability-unavailable-reason`; for `memory_pool.list` they are
   `memory-pools-empty-branch` or `memory-pool-rows-named`. An empty pool list is not row-shape
   evidence.
5. **ST10.** Each step reads back what it changed. In any arm other than `profiles`, ST10 runs
   only the description round trip, the non-VIOS MSP refusal and the memory-pool check; the
   VIOS MSP, processor-compatibility, sync and backup/restore round trips are SKIPs there. Restores run even after a failed assertion,
   and `cleanup="passed"` only when the restore read-back equals the restore source.
   - **Description.** Probe text, then restore the ST0 baseline; the existing manual-recovery
     row applies.
   - **MSP.** Targets the first `vioserver` by name from an `lssyscfg -F name,lpar_env` read.
     Read that VIOS's MSP immediately before the toggle; if the read fails, record SKIP and do
     not toggle. Restore the value read. No `ownership_override` is passed: an unstamped
     description passes the ADR 0092 guard (`authorize_lpar_ownership_description`), and a
     stamped one is a refusal the run records as FAIL. The non-VIOS refusal on the test partition stays a
     non-promoting check.
   - **Processor compatibility.** Read the default profile's `profile_mode` immediately before
     the change and set a supported mode other than it. Restore the value read, `default`
     included. Only modes the tool's schema accepts are used, because the CLI reads
     `POWER9_base` where the schema spells `POWER9_Base` (#1319). An original mode the tool
     cannot write back is a SKIP.
   - **Sync.** If the ST0 value is absent or not in `0|1|2`, record SKIP with the manual
     `chsyscfg … sync_curr_profile=<n>` command and do not enable. If the ST0 state is not
     `Not Activated`, record SKIP naming the activated-partition gap and do not enable. Otherwise run `enable`,
     check the read is `1`, then restore with the ST0 value's mode.
   - **Backup and restore.** Run only when `RunState.group == "profiles"`; any other dispatch,
     round2 and all included, records SKIP. Back up to `hmcpctl-live-st10` with `force=True`.
     Only if the backup PASSes, run `rstprofdata -l 3` from that file with
     `system_wide_restore_approved` and `ownership_override`. Two assertions compare the
     system before and after, each as a set of lines, because the HMC reorders a partition's
     profiles after a restore:
     - `profiles-unchanged-after-merge-current-wins` compares `lssyscfg -r prof -m <system>`;
     - `partitions-unchanged-after-merge-current-wins` compares `lssyscfg -r lpar -m <system>`.

     Together they show whether the type-3 merge is non-destructive, not that data was
     restored. A live run on 2026-10-05 showed that it is not: the merge resets a not-activated
     partition's `resource_config` from 1 to 0. The arm then re-applies each such partition's
     current profile with `chsyscfg -o apply` through `hmc_run_command`. `cleanup` is `passed`
     when the partition records match the pre-run read again.
   - **Memory-pool removal.** `memory_pool.remove` with an absent pool name is a non-promoting
     check that the refusal precedes `chhwres`.
6. **ST15** drops its sync call. Its proc-compat restore sets the ST0 baseline `profile_mode`;
   when that is absent it records the manual-recovery row instead, and when the tool cannot
   write it (#1319) it records a SKIP.
7. **Contract fixture.** The only captured shape no test covers is the resource-group calculated
   row with an empty `requested_lpar_names`. It becomes
   `tests/fixtures/live/cli-memopt-resgroup-calc.json`, tested in
   `tests/lpar/test_resource_group_affinity.py`. The sync modes are tested in
   `tests/lpar/test_lpar_profile.py`.
8. **Catalog.** Each operation gets an `implemented` record with the variant above. The
   exception is `memory_pool.remove`, which gets no observation. Observations are copied from
   the runner's emitted file under ADR 0126. Regenerate the runtime projection and
   `docs/tools/`, and update `CHANGELOG.md` and `.secrets.baseline`.
9. **Second environment.** ST4 alone runs read-only against one POWER11 system on a V11R2 HMC,
   from a separate worktree `.env`, with preflight and recovery. For the minimum-affinity and
   resource-group reads, the catalog keeps the observation from the environment where the
   capability is available. Every other operation keeps the mutation-boundary observation.

## Live gaps (durable record for this slice)

| Case | Prerequisite | State |
|---|---|---|
| `rstprofdata -l 1` and `-l 2` | an operator-approved window to rewrite all profiles | not run |
| non-empty `memory_pool.list` and positive `memory_pool.remove` | an AMS-capable system with an unused pool | not run |
| sync `enable` on an activated partition | an active test partition | not run |
| sync `suspend` | a scenario that activates the partition after suspending | automated only |
| `hmc_set_lpar_msp` on a VIOS | VIOS-aware ownership resolution (#1318) | failed live |
| a profile in mode `POWER9_base` | schema and read vocabulary agree (#1319) | not run |
| `memory_pool.remove` reference syntax | the tool sends `-a <pool_name>`; the reference removes the system's one pool with `chhwres -r mempool -o r`, so fixing it needs an AMS-capable system to capture against | catalogued `partial`, missing `reference-syntax-remove` |
| `snapshot.assess_affinity` | none; it issues no HMC command | not applicable |

## Success

- The 21 operations carry the row bindings and variants in the table, plus a maturity record.
- Every live observation in the catalog was emitted by a run at the branch's final `src/`
  closure, as reported by `just verification-report`, and is copied with its emitted `passed`
  or `failed` result. A closure-changing edit after a run forces that arm to re-run. A SKIP or
  non-promoting check is never copied as an observation.
- After the `profiles` run, an independent `lssyscfg` read of the test partition, all
  profiles, and the VIOS `msp` equals the pre-run read line for line. Two exceptions: the
  backup file, and the HMC's listing order of a partition's profiles.
- `hmc_sync_lpar_profile(mode=…)` renders `1`, `0` and `2`, and refuses any other value
  before I/O.
- No arm other than `profiles` dispatches `hmc_restore_lpar_profiles`.
- `just verify` and `uv run --no-sync prek run --all-files` pass.

## Failure model

1. **Actors and deployments:**
   - a local operator dispatching `live_profiles.py` from the operator's live-test host against the designated V10R3
     mutation-boundary HMC and system;
   - a read-only ST4 run against one V11R2 POWER11 system;
   - MCP callers of `hmc_sync_lpar_profile`;
   - CI, offline.
2. **Invariants and assets:**
   - every partition's profiles on the mutation-boundary system;
   - the VIOS `msp` setting;
   - the test partition's ownership-stamped description (ADR 0011);
   - the `hmc_sync_lpar_profile` schema;
   - the honesty of `maturity.json`.
3. **Accepted failure classes:**
   - One backup file stays on the HMC, overwritten by the next successful backup. Its cost is
     bounded, and no `rmprofdata` tool exists (row owned by #698).
   - An interrupted ST10 can leave one of the settings it changes (description, MSP, profile
     mode, sync) changed. `docs/live-testing.md` lists each manual restore.
   - A failed or interrupted `rstprofdata` leaves the operator to restore from that run's backup
     with `rstprofdata -l 1` after review. The runbook says not to re-run the arm before the
     profiles are confirmed, because the next backup overwrites that file.
4. **Covered elsewhere:**
   - the live gaps above;
   - affinity writes (#626) and migration (#631);
   - profile CRUD and save-current (#637);
   - environment isolation of tests (#461);
   - authorization semantics (ADR 0092, unchanged).
