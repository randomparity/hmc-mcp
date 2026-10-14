# Release claimed operations

## Problem and scope

Issue #1511 implements the retained claimed sets settled by
[ADR 0212](../../adr/0212-release-scoped-live-verification.md).
The initial set is the 15 operations in #871, with the operation/scenario
relationships recorded at the v0.1.0 tag. Gate policy belongs to #1478,
snapshot promotion to #1508, window authorization/dispatch to #1513, and
runbook changes, including `docs/development.md`, to #1514.

## Contract

Retain `docs/capabilities/releases/v0.1.0.json` and subsequent release files.
Each JSON object has exactly `format_version` (integer 1) and `operations`
(an array). Each row has exactly three nonempty strings: `operation`, `arm`,
and `scenario`. Operation IDs are unique within a file. An empty array is a
structurally valid empty claimed set; release eligibility remains #1478's concern.
Filenames are version labels beginning with `v`, ending in `.json`, and using
only ASCII letters, digits, periods, hyphens and plus signs.

The existing inventory validator owns parsing and catalog membership checks.
`load_release_claims(path, operations)` returns the validated rows, raising
`InventoryError` on invalid shape, duplicate keys/operations, unreadable JSON,
unknown references, or mismatched operation–arm–scenario relationships.
`validate_inventory` validates the JSON files in the required nonempty
`releases/` directory and incorporates errors into its existing report.

Runner metadata registers the seven scenarios needed by the first release
against their existing dispatcher subtasks (1, 24, 25 and 41). A claim's arm
must contain that subtask in `SUBTASK_GROUPS`. Its operation/scenario pair must
occur together in a `record_verified` call in that subtask's module, reading
literal operation IDs and literal or module-constant scenario IDs through AST.
This registers release routes without inventing a second arm list or requiring
prior successful evidence before a future release window. Further scenarios
can be registered beside the existing metadata when a claimed set needs them.
Collection alias `all` is accepted only where it actually contains the registered
subtask. The lpar-power guard requires its own arm; subtask 41 belongs only
to that arm, so its registration cannot admit another group.
No runner dispatch, assertions, authorization, catalog evidence, or runtime
projection behavior changes. This is an extension; no ownership migration or
compatibility path is needed.

## Success and validation

- The committed v0.1.0 set contains exactly the 15 #871 operation IDs and the
  tagged operation/scenario pairs, using round2, dedicated, bare-cec, lpar-power.
- `just capability-inventory` accepts that set and rejects malformed files,
  unknown operations/arms/scenarios, crossed known references, and duplicates.
- Tests exercise the loader, inventory integration and exact seed; controlled
  invalid relationships demonstrate rejection independently of evidence age.
- Focused inventory tests, runner contract tests and normal repository guards
  pass offline. No HMC call is part of this proof.

## Failure model

- Actors and deployments: contributors and CI validating a source checkout.
- Invariants and assets: retained claims name real catalog operations and
  source-declared evidence routes; malformed files cannot silently pass.
- Accepted failure classes: source changes may require updating claims/route
  registrations; this check does not execute hardware or prove assertions pass.
- Covered elsewhere: evidence freshness and release eligibility #1478;
  operator authorization and recovery #1513; installed projection ADR 0212.

## Threat model

- New boundary: contributor-controlled JSON enters the inventory checker.
  Existing boundary reused: repository Python source is trusted and parsed as AST.
- Actors: local contributors and CI; trusted repository code may already execute
  through registry discovery. No remote service or anonymous input is added.
- Controls: existing duplicate-key JSON loader, closed shapes, string checks,
  catalog membership and source-backed tuple validation; failures use existing
  inventory diagnostics. Metadata values never become shell commands or paths.
- Out of scope: malicious repository Python, filesystem replacement races and
  resource exhaustion by a local actor who controls the checkout; existing CI
  permissions and operator review own those execution boundaries.
