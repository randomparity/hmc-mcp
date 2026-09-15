# Reconcile quick-properties records — design

Issue #817.

## Problem

PR #814 (#811) changed `list_quick_properties`: a 200 carrying the `QuickProperty_Collection`
container and no name now returns `([], version)` rather than raising `HMCError`. ADR 0140's
Decision still states the old raise; PR #816's banner on 0140 covers only the return-type change
(ADR 0144), not this one. A spec clause and two executed plan records quote the same falsified
shapes, unpointed.

## Scope

Four prose files, no code/test/other-doc change:

- `docs/adr/0140-...md` — append a second Status banner beneath the existing one, in the
  `> **Amended by [ADR NNNN](...)** (date): ...` shape ADR 0141/0142 already use, recording that a
  nameless 200 carrying the container now returns rather than raising. Decision body untouched.
- `docs/workflow/specs/2026-09-14-discover-quick-properties.md:128-130` — strike the "200 carrying
  no non-empty `Nickname` ... raises `HMCError`" clause with a pointer to #811, matching the
  adjacent strike at `:131-132`.
- `docs/workflow/plans/2026-09-15-discover-search-parameters.md:174` and
  `docs/workflow/plans/2026-09-14-discover-quick-properties.md:73` — strike the stale
  `test_list_*_204_returns_no_names` names (and the second file's `([], "V1_0")`) with a pointer to
  the live renamed tests. Strike-and-point — the operator's disposition, not reopened here.

Out of scope, with owners: a "grep the old literal first" control (separate issue); correcting
merged code/tests from #811/#812/PR #814/PR #816 (already merged); codifying `Amended by` into
`adr.sh`'s `BANNER_PREFIX` (separate).

### Failure model

**Actors.** A human reader; `just adr-numbering`/`adr.sh`, which check filename, H1, and only the
`^> \*\*Superseded by` form.

**Invariants.** No runtime, persisted, or gated behavior depends on this prose; the asset is
record accuracy.

**Accepted.** The gate matches `Superseded by`, not `Amended by` — a wrong banner shape would pass
CI silently. Accepted: matched to the three existing `Amended by` banners by exact form.

**Covered elsewhere.** None.

## Success

1. ADR 0140 carries a second `Amended by` banner matching the 0141/0142 form; Decision unchanged.
2. The spec's raise clause is struck with a pointer to #811, in the `:131-132` form.
3. Both plan records are struck-and-pointed to the live test names and current return value.
4. `just verify` and `uv run --no-sync prek run --all-files` are green.

## Validation

| Contract | Mode | Evidence |
|---|---|---|
| ADR 0140 banner matches sibling form; Decision byte-unchanged apart from the append | `task-test-not-applicable` | No consumer parses banners; `adr-numbering` checks filename/H1 only. Diff-reviewed against the two sibling banners. |
| Spec clause struck with #811 pointer, matching the adjacent strike | `task-test-not-applicable` | Prose edit; no executable observation of a strike-through exists. |
| Plan records struck-and-pointed to the live test names | `task-test-not-applicable` | Prose edit; the named live tests are unmodified by this change. |
| Guardrails stay green | `focused-test` | `just verify`; `uv run --no-sync prek run --all-files` |
