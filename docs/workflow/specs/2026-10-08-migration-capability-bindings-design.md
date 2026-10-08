# Migration capability bindings

## Problem

The six `lpar.migrate*` and `lpar.remote_restart` records share twelve placeholder
rows instead of the REST jobs and supporting reads their handlers issue.
Issue #1411 requires truthful bindings while preserving coverage-child ownership.

## Scope

Replace only these six `row_ids` lists in `docs/capabilities/operations.json`.
Reuse the five existing LogicalPartition Migrate, MigrateValidate, MigrateAbort,
MigrateRecover and RemoteRestart job rows; the latter is filed under
`managedsystem-jobs` but documents the LogicalPartition endpoint. Both POWER10
and POWER11 references match `client_lpm.py:39` submissions.

Include ManagedSystem and LogicalPartition rows for selector/ownership reads and
`rest:jobs`/`rest:job-status` for conditional polling. Include `cli:commands/lssyscfg`
for ownership token reads on mutating paths; standalone validation does not run
that guard. Migrate and affinity include validation and migration jobs; abort,
recover and remote restart each include only their own submission job. Affinity
preflight consumes caller evidence locally and adds no separate reference row.

Preserve existing rows, source accounting, dispositions, registry identities,
maturity and test links. The existing validator remains the structural owner;
no runtime caller or ownership transition is required. Exclude finished PR1/PR2,
snapshot owner #1378, delegated lpar.modify owner #1386 and residual coverage
owners #698/#638/#658/#697/#680. No new rows, ADR, generator or dependency.

### Failure model

- Must prevent: wrong/missing submission or conditional support rows among the six records.
- Must detect: missing source-backed row IDs and disturbed unrelated catalog records.
- Accepted: offline semantic joins do not establish firmware acceptance or live job success.
- Deployment: checked-in POWER10/POWER11 catalog, current handlers and offline consumers.

## Success

The six row sets reflect the union of reachable requests, including conditional
validation, name/UUID resolution, ownership and polling. Unsupported placeholder
CLI commands and ChangeDefaultProfileName disappear from these records only;
their existing coverage-child dispositions remain. Projection and tool docs are
regenerated only if their recipes change output. PR3 closes #1411 after PR1/PR2.

## Validation

- Mode: focused-test. Parameterized exact sets for six records in
  `tests/scripts/test_check_capability_inventory.py`; original placeholder sets
  must fail, then `uv run --no-sync pytest tests/scripts/test_check_capability_inventory.py -q --no-cov` passes. Tests also require matched existing source-backed rows.
- Mode: task-test-not-applicable. Spec prose explains the bounded semantic choice;
  no executable consumer interprets its wording.
- Run `just capability-inventory`, regenerate `just capability-metadata` and
  `just tool-docs`, confirm only expected output, then `just verify` and
  `uv run --no-sync prek run --all-files` before push. No HMC contact.
