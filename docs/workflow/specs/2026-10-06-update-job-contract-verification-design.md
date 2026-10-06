# Update job contract verification (V9b)

Issue #1350, part of #633 (epic #620). Pattern: #627 / PR #1320.

## Problem

`update.console`, `update.list_ptfs`, `update.vios`, `upgrade.vios` and `update.firmware` are
each bound in `docs/capabilities/operations.json` to a whole family of update or HMC-upgrade
rows, most of which they never issue. None has a maturity record. The `update.firmware`
version gate has never been observed on a real HMC, and no test shows that it refuses a
*named* system before resolving it.

## Contract audit

Paths are relative to `docs/refs/hmc-rest-api-p11/jobs/`. Every job is a `PUT` JobRequest
(`../017-jobs.md:31`).

| Operation | Path | Parameters | Terminal result | Verdict |
|---|---|---|---|---|
| `update.console` | `managementconsole-jobs/121-…:17` | 13 names, `:24-36` = `ConsoleUpdateSource` | `:42-45`; `COMPLETED_OK` and `FAILED_BEFORE_COMPLETION` are in `TERMINAL_JOB_STATUSES` | matches |
| `update.list_ptfs` | `managementconsole-jobs/102-…:17` | none (`:22`) | bare `COMPLETED` (`:39`), terminal since #1202 | matches |
| `update.vios` | `virtualioserver-jobs/160-…:17` | `:24-37`; per-type requirements follow the samples `:47-379` | `stdOut` (`:43`) projected by `_with_vios_stdout` | matches |
| `upgrade.vios` | `virtualioserver-jobs/161-…:17` | `:24-37`; no `RestartVIOS`, no `IBMWebsite` (`:24`) | `stdOut` (`:43`) | matches |
| `update.firmware` | `managedsystem-jobs/065-…:17`, JSON `PUT` (`:59-61`) | `:28-49`; HMC 11.1.1111 minimum (`:12`) | `Result` (`:335`), `COMPLETED_WITH_ERROR` (`:307`) terminal | matches; `PartitionMigration` appears only in a sample (`:242`) and is not modelled |

`NOT_STARTED` conflicts: `121-…:42` and `102-…:36` call it a parameter-validation failure,
while `../016-job-status.md:24` calls it not yet initiated. `TERMINAL_JOB_STATUSES` follows
`016` because every job shares it, and a terminal `NOT_STARTED` would end every queued wait.
So a console update or PTF listing the HMC rejects in validation surfaces, with `wait=True`,
as the last-seen `NOT_STARTED` entry after the timeout. Which reading holds needs a live
rejected submission; it is a gap below, not a confirmed defect. The two tools' descriptions
say so, and tell the caller to read the job before submitting again.

No defect is confirmed in the five job contracts, so no `src/` behaviour changes. The unmodelled
`PartitionMigration` step appears in no parameter table, only in samples (`:242`, `:450`);
it is new PlatformUpdate scope, tracked by #680 through the PlatformUpdate row's disposition
in `rows.json`.

## Changes

1. **Rows.** Each operation binds only what its handler issues:

   | Operation | Rows |
   |---|---|
   | `update.console` | `updatemanagementconsole_managementconsole-job`, `rest:jobs`, `rest:job-status` |
   | `update.list_ptfs` | `listmanagementconsoleupdates_managementconsole-job`, `rest:jobs`, `rest:job-status` |
   | `update.vios` | `updatevios_virtualioserver-job`, `rest:managed-system`, `rest:managed-system/virtual-i-o-server`, `rest:jobs`, `rest:job-status` |
   | `upgrade.vios` | `upgradevios_virtualioserver-job`, the same four reads |
   | `update.firmware` | `platformupdate_managedsystem-job`, `rest:managed-system`, `rest:jobs`, `rest:job-status` |

   Job rows carry their `rest:jobs/<group>-jobs/` prefix. The ManagementConsole read the
   firmware gate makes has no row in `rows.json`.
2. **Maturity.** One record each, no evidence (`unevidenced`). Four are `implemented` with
   one variant each (`update-management-console-job`, `list-management-console-updates-job`,
   `update-vios-job`, `upgrade-vios-job`). `update.firmware` is `partial`: implemented
   `platform-update-job`, missing `partition-migration`. Regenerate the runtime projection and
   `docs/tools/`.
3. **Offline test.** `hmc_update_firmware` called with a system *name* on a V10R3 console
   raises the 11.1.1111 refusal, and neither a ManagedSystem request nor the PlatformUpdate
   `PUT` is made.
4. **Live check.** ST1 gains `_check_platform_update_refusal`, a non-promoting `state.record`
   check like the #1320 refusals. It reads the version from the ST1 console read and calls
   `hmc_update_firmware` only when that version parses and is below 11.1.1111; otherwise it
   records SKIP and makes no call. It passes an absent synthetic system name, so on an HMC
   past the gate the call still stops at a name lookup before any `PUT`. PASS needs a
   failure carrying both `requires HMC 11.1.1111` and `below the minimum`; any other
   outcome, success included, is FAIL. It is never copied into `maturity.json`. Two readers
   still count the dispatch: `just scenario-gap` lists `update.firmware` as exercised, and the
   `scripts/live_test_evidence.py` matrix shows `hmc_update_firmware` PASS. The PR cites that
   matrix and says the row is the refusal, not a submit.

## Failure model

1. Actors and deployments: a maintainer running ST1 singly from the lab host against the
   V10R3 boundary HMC; CI running the unit suite with no HMC.
2. Invariants and assets: no update, fetch or job may start on lab hardware; maturity must
   not promote an operation whose positive path never ran.
3. Accepted failure classes: the check logs on and reads the ManagementConsole feed — a
   session and a read, not a managed-state change; ST1 in `round2` and `all` also runs the
   check, accepted because it is guarded twice and mutates nothing.
4. Covered elsewhere: readiness checks and catalogs (#698); HMC upgrade (#683); console-data
   backup (#682); new PlatformUpdate parameters (#680).

## Live gaps

| Case | Prerequisite | State |
|---|---|---|
| `update.console` submit | a disposable HMC, an update image, a maintenance window | not run |
| `update.list_ptfs` | HMC outbound access to IBM; the job makes the HMC fetch | not run, never invoked |
| `update.vios`, `upgrade.vios` | a disposable VIOS, images, free disks for upgrade, a window | not run |
| `update.firmware` submit | POWER11 with HMC V11.1.1111 or later, images, a window | not run |
| `update.firmware` on V10R3 | none | non-promoting check |
| `NOT_STARTED` on a rejected console update or PTF listing | a live submission the HMC rejects in validation | not run |

## Success

- The five operations carry exactly the rows in Changes 1 and the records in Changes 2.
- The offline test fails if the gate moves after name resolution or the `PUT`.
- A live ST1 run at the PR head records the firmware check as PASS. Recovery, given that
  run's results document, exits 2 listing only subtask 1 as NOT WITNESSED and nothing as
  stranded, because ST1 dispatches no mutating call.
- `just verify` and `uv run --no-sync prek run --all-files` pass.
