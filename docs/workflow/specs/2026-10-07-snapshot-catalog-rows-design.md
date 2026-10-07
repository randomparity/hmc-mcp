# Snapshot operation catalog rows and live capture (#1378)

## Problem

The three `snapshot.*` operations below are bound to twelve job placeholder rows no
handler issues and carry no `maturity.json` record. Capture reads the HMC; inspect and
validate parse a local document.

## Scope

- `snapshot.capture` row_ids become the reads `capture_lpar_snapshot` issues:
  `rest:managed-system`, `rest:managed-system/logical-partition` (REST reads),
  `cli:commands/lssyscfg` (profile record, minimum-affinity policy),
  `cli:commands/lsmemopt` (scores), `cli:commands/lshmc` (`lshmc -V` before the
  resource-group query). Its ManagementConsole feed read has no non-job catalog row;
  the composite reason says so rather than binding an unrelated row.
- `snapshot.inspect` and `snapshot.validate` bind no rows and carry the
  `snapshot.assess_affinity` style composite reason (local parsing, no HMC command).
- `maturity.json` gains `implemented` records for all three (variants `hmc-capture`,
  `local-snapshot`), `evidence: []` for inspect/validate; regenerate
  `src/hmcpctl/_operation_maturity.json` and `docs/tools/`.
- ST1 gains `_capture_snapshot`: read the test partition's default profile name with
  `hmc_get_lpar_proc_compat` (recorded, non-promoting), then `record_verified` an
  `hmc_snapshot_capture(..., profile_name=<name>)`, scenario `st1-lpar-snapshot`:
  `snapshot-names-partition` (`source.lpar` uuid and name), `snapshot-names-system`
  (`source.system.uuid`), `profile-captured` (`configuration.profile_name` equals the
  name; `native.data` non-empty), `scores-name-partition`
  (`observations.scores.data.current.lpar.lpar_name`). No name records FAIL, not SKIP.
- Live: `live_test_preflight.py`, then `live_test_runner.py 1 --results-file
  test-results-st1.json` on the pushed clean head, then `live_test_recovery.py`. The
  observation is copied into the catalog whether it passed or failed; a failure is reported.

### Failure model

1. Actors and deployments: a local operator running the live runner against the
   boundary system; CI running the offline gates.
2. Invariants: the new step calls only `effect="read"` tools (ST1's one destructive
   tool is the existing refusal check against an absent system); the eight rows the
   placeholders free stay `coverage-child` under #638; catalog observations stay
   free of lab identifiers.
3. Accepted: the ManagementConsole read stays uncatalogued (no row exists to bind);
   inspect/validate keep `evidence: []` (no evidence channel exists for local tools).
4. Covered elsewhere: orphaned job rows — #638; console placeholder rows — reported only.

## Success

- `just capability-inventory`, `tool-docs-check` and `doc-freshness` pass.
- ST1's guard lists `st1-lpar-snapshot` with its four assertion ids.
- A live ST1 run's `snapshot.capture` observation is in `maturity.json`.

## Validation

- Rows and composite reasons: `focused-test` — `just capability-inventory` exits 0.
- Maturity records and projection: `focused-test` — `just capability-inventory` after
  `just capability-metadata` (red: stale projection or invalid record).
- Generated tool docs: `focused-test` — `just tool-docs-check`.
- ST1 capture step: `focused-test` — `tests/test_live_runner.py` scenario-guard test
  (red until declared) plus a test that a violating snapshot fails each assertion.
- Live observation: `task-test-not-applicable` — hardware evidence; `just
  capability-inventory` checks only its shape.
