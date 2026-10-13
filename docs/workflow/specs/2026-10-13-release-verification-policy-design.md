# Release-scoped verification policy

## Problem and authority

Issue #1507 records the operator's 2026-10-13 decision to move live verification
from development-commit currency to a repeatable release stage. The approved
campaign scope supplies the frozen-runtime choice and sibling ownership.
[ADR 0212](../../adr/0212-release-scoped-live-verification.md) is the normative
record; this spec describes its bounded documentation change.

## Scope and architecture

Add ADR 0212 and partial-supersession status banners to ADR 0127 and ADR 0131.
Keep the older records' remaining text as history. The new decision separates
observation eligibility at a release candidate from the verification state
presented by an installed release. It retains computed closure matching,
closed-shape evidence, asserted postconditions, clean-tree collection,
operation identity joins, and authorization independent of maturity.

Record ad-hoc eligibility, reviewed-branch promotion, split projection,
tag-time release-mode validation, claimed-only gating with a 90-day ceiling,
frozen installed state, removal of scheduled stale failure, and replay fixtures
as future work. Record the approved snapshot and versioned claimed-set locations,
sparse absence semantics, and opt-in artifact release-mode distinction.
No runtime, schema, command, generated-resource, CI, or runbook implementation
belongs to this slice. Those owners are #1508, #1509, #1510, #1511, #1512,
#1513 and #1514; replay fixtures remain future work under #1506.

## Global Constraints

Policy prose only; no new dependency or toolchain floor. ADR assignment is 0212.
No live hardware execution, tag, or merge is authorized. Keep plans untracked.

## Failure model

- Actors and deployments: maintainers reading the accepted decisions and
  sibling implementers consuming their policy in this repository.
- Invariants and assets at stake: authority for the reversal, release evidence
  meaning, preserved collection/authorization safeguards, and honest implementation status.
- Accepted failure classes: runtime still follows the old model until the named
  sibling changes land; this policy-only slice explicitly does not claim deployment.
- Covered elsewhere: projection and runtime reader #1508; deterministic renderer #1509;
  CI #1510; claims #1511;
  promotion #1512; authorized window #1513; runbook #1514; replay future #1506.

## Success and validation

The ADR and two banners cover issue #1507's criteria and the recorded operator
choices without changing the older ADR bodies. Human review checks policy
meaning and provenance; wording snapshots would not prove either.
Existing `just adr-numbering` validates the new record's filename and heading.
`just static` and the installed prek hooks check documentation interactions.
Repository-local pre-push instructions additionally require one final `just verify`
and `UV_NO_SYNC=1 uv run --no-sync prek run --all-files`; their results are
reported as repository compliance, not proof of the future runtime behavior.
