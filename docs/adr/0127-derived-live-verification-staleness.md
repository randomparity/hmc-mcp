# ADR 0127: Derive live-verification staleness from the operation's import closure

## Status

Accepted

Supersedes [ADR 0126](0126-operation-keyed-maturity-evidence.md). Its implementation-state
model — `absent`, `partial`, `implemented`, with explicit implemented and missing scope
objects — is carried forward unchanged. Its evidence, currency and promotion model is
replaced by this record.

## Context

ADR 0126 stored each observation's currency (`current` or `stale`) and computed one SHA-256
over every tracked file under `src/`, `scripts/`, `pyproject.toml` and `uv.lock` — 209
files, about 2.0 MB — as the implementation fingerprint. A current observation whose
fingerprint no longer matched was a validation *error*, and `just capability-inventory` is a
member of `static`, which gates every commit through the prek hook and every CI leg.

Measured on this repository on 2026-09-06: 1,441 of 2,594 commits in the preceding 90 days
touch that scope, with a median of six minutes between them; 59 merges to `main` touched it
in 30 days. Appending one comment to one file changes the digest. Both operations in the
catalog have zero observations, which is the only reason the error has never fired: the
first current observation on any channel would have turned the build red for everyone
until a human hand-edited it stale, and a promoting live observation could never merge,
because another pull request would land between the test run and the merge.

The operator's restated goal is narrower than 0126 answered: know whether each operation
has been validated by a live test, and know when that validation stops counting — without
blocking a developer who pushes a change and then asks a test team to validate it.

## Decision

**Staleness is derived when the catalog is read, never stored and never a validation
error.** An observation records what was true when it was made; the validator computes
whether it still holds.

Two triggers, either of which makes an observation stale:

1. **The operation's import closure changed.** The runner records the SHA-256 of the
   handler module and every `src/hmc_mcp/` module it transitively imports, resolved by
   walking `import` statements in the source. The validator recomputes it. The closure is
   computed, not authored, which answers 0126's objection to per-operation dependency
   lists: nothing is omitted by hand because nothing is listed by hand.
2. **The observation is older than 90 days.** Code-change detection sees only this
   repository; the age ceiling is the one trigger that sees the HMC change underneath it.

The observation shrinks to closed-shape fields: operation, result, scenario id, tested
commit, observation time, HMC release, hardware family, cleanup disposition, closure
fingerprint, and assertion ids drawn from a fixed pattern. The two environment strings are
the only free text the runner ever writes. There is no stored `currency`, `invalidated_by`,
`promotion`, `implementation_fingerprint`, or per-observation `scope`.

A `not-run` row is the exception, and it is not runner-emitted: its `reason`, `prerequisites`
and `obligation` are human prose, length-capped and IPv4-rejected but not closed-shape.
Pull-request review is the control there. The spec's threat model states that rather than
letting the closed-shape claim cover a field it does not reach.

A `passed` observation that is not stale is a current promotion; no other state promotes.
The runner emits observations only from a clean tree, only from the `record_verified` path
that carries asserted postconditions, and never writes them into the catalog — a human
copies them in.

`just verification-report` prints every operation's state and a summary. On a pull request
or push it exits 0 and emits workflow warnings; on the weekly scheduled run it fails when
anything is stale. `ci.yml` permissions stay `contents: read`.

## Consequences

Recording evidence no longer breaks the build, and evidence stops counting exactly when its
operation's implementation changes or its age exceeds the ceiling — not when an unrelated
file gains a comment. A change to a module every handler imports (`_app.py`,
`tool_registry.py`) still invalidates every observation, which is correct and is the
behaviour 0126 wanted preserved.

The validator proves shape and derives currency; it does not prove an observation is true.
Trust rests on the record being small, closed-shape, and reviewed in the pull request that
commits it. The runner refuses to write observations to a path `git check-ignore` does not
claim, so committing an observation is a deliberate act. The check is what makes that true:
`--results-file` lets an operator name any stem, and the atomic write's own temp file needs a
pattern of its own, so a fixed gitignore line alone would not establish it.

The weekly red run is the only forcing function. A stale observation that nobody re-runs
stays visibly stale in every report; nothing promotes it back. Scenario coverage remains
hand-written until #706.

## Considered & rejected

- **Keep the repository-wide fingerprint and only downgrade the error to a warning.**
  verified: on `ded24a77`,
  `git log --since='90 days ago' --oneline -- src scripts pyproject.toml uv.lock | wc -l`
  returned 1441 and `git log --since='90 days ago' --oneline | wc -l` returned 2594, median
  0.1 h apart; every observation would be stale within the hour whatever the severity, so the
  warning would be permanent noise.
- **Fingerprint at release-tag granularity.** judgment: rejected by the operator as shifting
  re-validation to release time and endangering the release schedule.
- **Hand-authored per-operation dependency lists.** verified: ADR 0126, Considered &
  rejected, "Author a per-operation dependency list" — an omission silently preserves stale
  evidence. A computed closure has no authored list to omit from.
- **Derive staleness from `git log <sha>..HEAD -- <closure>`.** verified:
  `tests/test_ci_pipeline.py::test_active_ci_checkouts_with_project_uv_do_not_fetch_full_history`
  asserts no `fetch-depth` on any active checkout (ADR 0033), so history is unavailable
  where the report runs. A stored hash compares on a shallow checkout.
- **Automatically open a tracking issue from the weekly run.** verified:
  `test_github_ci_uses_the_local_gates_with_least_privilege` requires `ci.yml`'s only
  `permissions:` block to be exactly `contents: read`; `issues: write` is a permission
  widening the operator declined.
- **Keep 0126's twenty-field observation with structured assertions.** judgment: two
  independent design-review passes found its redaction gate rejected ordinary strings and
  admitted addresses and serials; closed-shape fields with no prose remove the detector's
  job rather than fixing the detector.
- **Do nothing.** verified: `scripts/check_capability_inventory.py:940-955` on `ded24a77`
  appends `stale implementation fingerprint` to `errors` for any current attempted
  observation whose fingerprint differs from now, and `errors` fails the recipe; the first
  recorded observation would gate every commit.
