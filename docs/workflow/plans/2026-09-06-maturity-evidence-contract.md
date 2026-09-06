# Implement the operation maturity and evidence contract

Goal: add a sparse, validated operation-keyed maturity catalog whose scoped evidence
cannot be mistaken for runtime eligibility or broader live verification.

Architecture: extend the existing offline capability inventory with one JSON artifact and
one validation path. The existing validator remains the only reader and guardrail; no
runtime package module imports the catalog. Representative records establish implemented
and partial states while all unrecorded operations remain explicitly unknown to consumers.

Tech stack: Python 3.11 standard library, UTF-8 JSON, pytest, Ruff, ty, just, and prek.

Expected implementation size: 360–560 changed lines (M) — derived from one catalog,
conditional validation in the existing script, focused fixtures/tests, and README updates.

## Global Constraints

- Python 3.11+ and only the standard library for validation.
- Host x86_64; declared CI targets amd64 and arm64; relationship included.
- JSON is UTF-8, versioned, deterministic, duplicate-key rejecting, and public-safe.
- No runtime import, dependency, migration, hardware mutation, safety change, new issue,
  or unrelated API work.
- Branch: `feat/maturity-evidence-contract-622`; base branch: `main`.
- Guardrails: `just capability-inventory`; `just adr-numbering`; `just verify`;
  `uv run --no-sync prek run --all-files`.

## Resume facts

- Issue and scope token: `#622`, `q622-a9e980a4`.
- Repository: `randomparity/hmc-mcp`.
- Current phase: design review confirming pass; build follows the scope audit.
- Routed review depth: iterating.
- Open findings: design review iteration 1 found four contract gaps; all four are
  accepted-fixed in the design set: canonical scope/revision identity, trusted promotion
  provenance, checkable implementation invalidation, and live-gap obligations. The
  operator authorized one additional pass after iteration 2; its four blockers and one
  note are accepted-fixed by the current design edit.
- Review deferrals: none before design review.
- Guardrail observations: `just adr-numbering` and `just doc-freshness` passed after
  the ADR/spec commit; commit hooks passed every configured static hook.

## File map

- Create `docs/capabilities/maturity.json`: admission-policy marker and representative
  records for `system.list` and `sriov.set_mode`.
- Modify `scripts/check_capability_inventory.py`: load and validate the maturity artifact,
  join operation IDs, enforce conditional evidence rules, and report the record count.
- Modify `tests/scripts/test_check_capability_inventory.py`: build valid maturity fixtures
  and exercise unknown, mixed, stale, regression, and false-promotion cases.
- Modify `docs/capabilities/README.md`: document the fourth artifact, sparse unknown
  semantics, evidence promotion, and runtime-admission separation.

## Task 1: Validate sparse maturity and evidence records

This task makes the representation executable and proves the negative promotion rules.

### Interfaces

Consumes the existing function:

```python
validate_inventory(
    root: Path,
    registry: Collection[RegistryTool],
    *,
    repo_root: Path = ROOT,
) -> Report
```

and the `operations.json` records whose `operation` field is the stable join key.

Adds `MATURITY_STATES = {"absent", "partial", "implemented"}`,
`EVIDENCE_CHANNELS = {"contract-review", "automated", "live"}`,
`EVIDENCE_RESULTS = {"not-run", "skipped", "failed", "passed"}`, and
`EVIDENCE_CURRENCY = {"current", "stale"}`.

Adds:

```python
_validate_maturity(
    records: Sequence[dict[str, object]],
    operation_ids: Collection[str],
    errors: list[str],
) -> None
```

Later code relies on it to reject invalid records without returning a second report type.
Adds `implementation_fingerprint(repo_root: Path) -> str`, hashing tracked runtime
source, scripts, and dependency manifests by sorted path and length-prefixed bytes.
Extends `Report` with `maturity_operation_count: int`; `main()` prints that value on the
structural-validity line. No runtime interface is added.

### Verification

- Contract: sparse absence is valid unknown state and valid operation records join to F1.
  Mode: focused-test. Add `test_sparse_maturity_allows_unknown_operations` and
  `test_maturity_rejects_unknown_and_duplicate_operation_ids`; the red observation is
  `InventoryError` for missing `maturity.json` or no errors for malformed join data because
  the current validator does not load it. Green command:
  `uv run --no-sync pytest tests/scripts/test_check_capability_inventory.py -q`.
- Contract: implementation-state scope invariants.
  Mode: focused-test. Add a parametrized `test_maturity_enforces_implementation_scope`
  covering absent, partial, and implemented plus each invalid empty/non-empty pairing and
  overlapping implemented/missing scope; the red observation is that malformed pairings
  pass. Use the same focused green command.
- Contract: all evidence states and channel-specific required fields.
  Mode: focused-test. Add `test_maturity_accepts_all_evidence_results` and
  `test_live_pass_requires_scoped_postconditions_and_cleanup`; the red observation is that
  `not-run`, skip, failure, and false live-pass shapes are not checked. Use the same focused
  green command.
- Contract: live gaps retain their planned scenario and owned obligation.
  Mode: focused-test. Add `test_live_not_run_requires_prerequisites_and_obligation`; the
  red observation is that an unavailable scenario can omit all three. Use the same focused
  green command.
- Contract: no format-1 authored claim is promotion eligible and implementation changes
  are mechanically detectable.
  Mode: focused-test. Add `test_format_one_rejects_promotion_eligible` and
  `test_implementation_fingerprint_changes_with_shared_source`; the red observation is
  that a mocked path, issue URL, opt-in marker, or transport-only assertion can claim live
  promotion and source changes leave no checkable invalidation signal. Use the same focused
  green command.
- Contract: current/stale uniqueness and environment-scoped mixed evidence.
  Mode: focused-test. Add `test_maturity_preserves_stale_pass_before_current_regression`
  and `test_maturity_keys_live_currency_by_environment`; the red observation is that two
  current observations with the same scope are accepted and different environments cannot
  be distinguished. Use the same focused green command.
- Contract: structural identity is independent of JSON object member order.
  Mode: focused-test. Add `test_maturity_identity_normalizes_scope_and_environment`; the
  red observation is that reordered members create separate current slots. Use the same
  focused green command.

### Steps

1. Extend `_minimal_inventory()` to write a valid empty `maturity.json`, and add builders
   that return one exact uniform evidence object. Keep all fixture values anonymous and
   use full 40-character lowercase revisions.
2. Add the sparse/unknown and operation-join tests. Run the focused command and require
   failures showing `maturity.json` is ignored or an unknown operation is accepted.
3. Change `validate_inventory()` to load `maturity.json`, require format version 1 and
   `admission_policy == "existing-runtime-guards"`, collect unique operation IDs from
   `operations.json`, and call `_validate_maturity()`. Add the report count and update every
   early `Report(...)` construction. Re-run the focused command; the join tests pass.
4. Add implementation-state tests, run them red, then validate exact record and nested
   implementation keys, normalized scope objects, set disjointness, and state/list
   combinations. Errors name `maturity operation <id>`. Re-run the focused command green.
5. Add result/channel tests, run them red, then validate exact observation keys and the
   conditional null/non-null rules from the spec. Use `datetime.fromisoformat()` after
   replacing terminal `Z` with `+00:00` and require UTC; use a full-SHA regex. A live pass
   must have environment, assertions, and successful or unnecessary cleanup. Non-live
   evidence must have `environment` and `deployed_revision` set to `null`. Re-run green.
6. Add live-gap tests, run them red, then require a live `not-run` observation to retain
   scenario identity, prerequisites, environment, and a catalog obligation exactly equal
   to `<operation>#<observation-id>` while remaining explicitly non-promoting. Accept an
   optional positive issue number only as a pointer; do not claim offline open-state proof.
7. Add promotion/fingerprint tests, run them red, then implement the deterministic
   normalized implementation fingerprint and reject `promotion.eligible=true` for format
   1. This leaves issue #623 a named extension point without trusting an arbitrary path or
   URL. Require the stored fingerprint on every current attempted observation to match the
   computed value. Re-run green.
8. Add stale/regression and environment-key tests, run them red, then enforce catalog-wide
   evidence-ID uniqueness, current observations with `invalidated_by == null`, stale
   observations with a valid invalidator, and one current observation per canonical tuple
   `(channel, scope-identity, scenario-id, environment-field-tuple)`. Construct identities
   from parsed field tuples rather than serialized JSON. Re-run green.
9. Create `maturity.json` with `system.list` as implemented and `sriov.set_mode` as partial.
   Give each evidence channel an honest current `not-run` observation because existing test
   links and narrative live records omit fields this contract requires. Run
   `just capability-inventory`; expect exit 0 and a structural line naming two maturity
   operations without changing the capability-coverage result.
10. Re-read the code for ≤100-line functions and complexity ≤8. Split validation by
   implementation, observation shape, and current-key checks if needed. Run `just lint` and
   `just typecheck`; expect both commands to exit 0 with no warnings.
11. Confirm the tests bite: temporarily make a live `passed` observation with empty
   assertions in a fixture and require the focused test to fail, then restore the valid
   fixture and require it to pass. Commit as
   `feat: validate operation maturity evidence`.

### Acceptance criteria

- The existing gate rejects malformed or contradictory maturity data and unknown joins.
- Sparse absence remains valid unknown state and does not change F1 completeness.
- Format 1 rejects every promotion claim until a trusted producer validator exists.
- Different live environments can carry different current results.
- Runtime/source/dependency changes alter the implementation fingerprint conservatively.
- Live gaps always retain a locally resolvable catalog owner; issue pointers are optional.

Rollback is a normal Git revert; no external or runtime state changes.

## Task 2: Document the installed catalog contract

This task makes the checked artifact usable without repeating the ADR in generated docs.

### Interfaces

Consumes the exact `maturity.json` keys and validator behavior from Task 1. It changes no
Python signature. Later issues #623 and #624 rely on the README's channel, currency,
unknown, and admission explanations matching the validator.

### Verification

- Contract: README instructions invoke the existing installed guard and accurately name
  the checked artifact and sparse semantics.
  Mode: task-test-not-applicable. The README prose has no dedicated executable consumer;
  snapshotting wording would test incidental text. Its command is independently exercised
  by Task 1 and the repository documentation/static guardrails.

### Steps

1. Update `docs/capabilities/README.md` to name `maturity.json`, explain absent rows as
   unknown, list the independent evidence channels and current/stale rule, and state that
   `existing-runtime-guards` neither grants nor revokes admission.
2. State that format 1 admits no trusted promotion, and that mocks, skips, opt-in, issue
   closure, transport-only success, and another live environment do not promote live
   evidence because trusted provenance is absent, not because free-text assertions prove
   postconditions. Explain live-gap prerequisites and obligations, then point maintainers to
   `just capability-inventory` for validation.
3. Run `just capability-inventory`, `just doc-freshness`, and `just adr-numbering`; expect
   all three to exit 0. Re-read the diff and scan authored public files for hostnames, IP
   addresses, internal domains, location strings, serial numbers, and credentials.
4. Commit as `docs: explain maturity evidence catalog`.

### Acceptance criteria

- A maintainer can distinguish unknown, not-run, stale, failed, and passed from the README.
- The README makes no runtime-admission or live-verification claim beyond the catalog.

Rollback is a normal Git revert; the JSON and validator commit may remain independently
valid if the prose commit is reverted temporarily.

## Final verification

Run `just verify`, then `uv run --no-sync prek run --all-files`, each bare and requiring
exit 0. Inspect the branch diff from `git merge-base HEAD origin/main`, verify no runtime
package or safety file changed, and retain the exact commands and results for the forge
handoff. CI remains the authoritative Python 3.11–3.14 and amd64/arm64 matrix proof.
