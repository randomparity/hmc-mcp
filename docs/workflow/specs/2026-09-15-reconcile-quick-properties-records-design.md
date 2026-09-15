# Reconcile quick-properties records — design

Issue #817.

## Problem

PR #814 (#811) changed `list_quick_properties`: a 200 carrying the `QuickProperty_Collection`
container and no name now returns `([], version)` rather than raising `HMCError`. ADR 0140's
Decision still states the old raise unconditionally. The one existing banner on 0140 (Amended by
ADR 0144, the record the shipped code itself cites) describes the new signature but never says the
raise claim no longer holds here. A spec clause and two plan records quote the same falsified
shapes, unpointed.

## Scope

Four prose files, no code/test/other-doc change:

- `docs/adr/0140-...md` — expand the existing `Amended by [ADR 0144](...)` banner (0144 is 0140's
  only governing amendment; no record here stacks two banners from one source), adding: the raise
  claim no longer holds when the container is present with nothing under it — that case now
  returns `([], version)`. Decision body untouched.
- `docs/workflow/specs/2026-09-14-discover-quick-properties.md:128-130` — strike the "raises
  `HMCError`" sentence of the "200 carrying no non-empty `Nickname`" bullet, pointing to ADR 0144
  (the record `core.py` cites for this return), matching the adjacent strike's pointer at
  `:131-132`. The container-absent sub-case still raises, unchanged.
- `docs/workflow/plans/2026-09-15-discover-search-parameters.md:174` and
  `docs/workflow/plans/2026-09-14-discover-quick-properties.md:73` — strike the stale
  `test_list_*_204_returns_no_names` names (and the second file's `([], "V1_0")`) with a pointer to
  the live renamed tests. Strike-and-point — the operator's disposition, not reopened here.

Out of scope, with owners: a "grep the old literal first" control (separate issue); correcting
merged code/tests from #811/#812/PR #814/PR #816 (already merged); codifying banner-content
validation into `scripts/check_adr_numbering.py` (separate — see Failure model).

### Failure model

**Actors.** A human reader; `just adr-numbering`, which checks filename/H1 only.

**Invariants.** No runtime, persisted, or gated behavior depends on this prose; the asset is accuracy.

**Accepted.** No gate checks banner content, so a malformed one passes silently; matched to the
existing banners by exact form instead. **Covered elsewhere.** None.

## Success

1. ADR 0140's existing banner is expanded, not duplicated, to state the raise claim no longer holds
   for the container-present-empty case; Decision section otherwise unchanged.
2. The spec's raise sentence is struck with a pointer to ADR 0144, in the `:131-132` form.
3. Both plan records are struck-and-pointed to the live test names and current return value.
4. `just verify` and `uv run --no-sync prek run --all-files` are green.

## Validation

| Contract | Mode | Evidence |
|---|---|---|
| ADR 0140 banner expanded, not duplicated; Decision byte-unchanged apart from the append | `task-test-not-applicable` | No consumer parses banners; diff-reviewed: exactly one `Amended by` block remains. |
| Spec clause struck with ADR 0144 pointer, matching the adjacent strike | `task-test-not-applicable` | Prose edit; no executable observation of a strike-through exists. |
| Plan records struck-and-pointed to the live test names | `task-test-not-applicable` | Prose edit; the named live tests are unmodified by this change. |
| Guardrails stay green | `task-test-not-applicable` | `just verify` / `prek run --all-files` are themselves the observation (precedent: `2026-09-07-interrupt-test-host-derived-budgets-design.md:58-59`). |
