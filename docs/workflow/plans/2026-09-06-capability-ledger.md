# Implement the versioned CLI/REST capability ledger

Goal: turn the four frozen IBM snapshots into a validated, reviewable capability
inventory and reconcile the current registry to it.

Architecture: checked-in JSON is the durable source of truth. A standard-library
Python validator owns schema, references, completeness, corpus hashes, and the
registry join; it does not affect runtime server behavior. The source corpora stay
outside Git and are supplied only to the explicit provenance verification arm.

Tech stack: Python 3.11 standard library, JSON, pytest, just, and prek.

Expected implementation size: 9000–15000 changed lines (L) — measured candidates include
832 topic records, 2544 command synopsis lines, 4696 command option rows, 5125 REST table
rows, and 1300 REST fenced blocks before normalized capability rows, validator, and tests.

## Global Constraints

- Artifacts are UTF-8 JSON with integer format version 1 and stable IDs.
- CI targets amd64 and arm64 on Python 3.11, 3.12, 3.13, and 3.14.
- Add no dependency and change no runtime registry, authorization, admission, or
  maturity behavior.
- Every source topic is classified; navigation and overview/schema topics do not
  count as executable operations.
- A POWER10/POWER11 document delta records snapshot presence only.
- Every capability row has exactly one disposition. Unknown and proposed-exclusion
  rows prevent a complete-coverage result.
- Every current registry operation has a join or an explained repository-specific
  composite. Generic transport and arbitrary-command paths do not count as dedicated
  coverage.
- Public artifacts contain no local paths or private identifiers.
- Guardrails are `just verify` and `uv run --no-sync prek run --all-files`.
- Branch `feat/capability-ledger-621`; base branch `main`; scope token
  `q621-b3084fca`; routed review depth `iterating`; open findings and deferrals: none.

## File map

- `docs/capabilities/corpora.json`: corpus identities and complete topic manifest.
- `docs/capabilities/rows.json`: normalized semantic capability rows.
- `docs/capabilities/operations.json`: registry-operation reconciliation.
- `docs/capabilities/README.md`: field meanings and honest interpretation boundary.
- `scripts/check_capability_inventory.py`: structural and corpus validator.
- `tests/scripts/test_check_capability_inventory.py`: focused behavior tests.
- `justfile`, `.pre-commit-config.yaml`, `tests/test_ci_pipeline.py`: static gate wiring.

## Task 1: Implement the validator contract

This task establishes the reader before producing the full dataset.

Interfaces:

- `load_json(path: Path) -> dict[str, object]` rejects malformed or duplicate-key JSON.
- `discover_registry() -> tuple[RegistryTool, ...]` returns tool name, operation ID,
  unwrapped handler module/name/signature, and registered MCP surface.
- `validate_inventory(root: Path, registry: Collection[RegistryTool]) -> Report`
  returns counts, unknown IDs, and errors without reading external corpora.
- `verify_corpora(root: Path, sources: Mapping[str, Path]) -> list[str]` validates four
  named regular-file trees, exact relative paths, SHA-256 values, and regenerated source
  units without following symlinks.
- `Report.complete` is false when any error, unknown, or proposed exclusion remains.
- Task 2 supplies the three JSON files; Task 3 wires the command into repository gates.

Verification:

- Mode: focused-test. Schema and reference integrity are observed by tests for duplicate
  keys/IDs, dangling topic/source-unit/row references, malformed dispositions, missing
  fields, and an unaccounted registry operation. Before implementation these imports fail. Green:
  `uv run --no-sync pytest -q tests/scripts/test_check_capability_inventory.py --no-cov`.
- Mode: focused-test. Corpus integrity is observed by missing, extra, changed, symlink,
  traversal, and omitted regenerated-unit fixtures. Before implementation the verification
  entry point is absent.
  Green: the same focused command.
- Mode: focused-test. Completeness state is observed by fixtures containing unknown and
  proposed-exclusion rows. Before implementation no report exists. Green: the same command.

Steps:

1. Add focused tests with minimal JSON fixtures and confirm import/contract failures.
2. Add immutable report records, strict JSON loading, and cross-record validation.
3. Add confined source-root traversal and byte hashing, rejecting non-regular paths.
4. Add structured registry discovery and validate handler signatures, claimed surfaces,
   source-parameter mappings, and repository-relative implementation/test paths.
5. Add a CLI whose zero exit means structurally valid and whose summary separately states
   whether semantic coverage is complete. Corpus mismatch and structural errors exit nonzero.
6. Run the focused command; expect all tests to pass. Introduce one missing source unit,
   registry operation, handler parameter, and test path fault in turn, observe failures,
   restore, and rerun green.
7. Commit as `feat: validate capability inventory`.

Acceptance: invalid records fail with an artifact, record ID, and correction; valid unknowns
remain visible without being treated as structural corruption.

## Task 2: Author and reconcile the four corpora

This task creates the reference denominator independently of current implementation.

Interfaces:

- The three JSON roots use `format_version: 1` and arrays named `corpora`, `topics`,
  `source_units`, `rows`, and `operations` as specified in ADR 0125.
- Stable topic IDs combine corpus ID and source slug. Stable capability IDs describe
  semantic operation and mode; they never depend on the current tool name.
- `source_refs` cite source-unit IDs; `parameters` name distinct inputs and constraints;
  `releases` records snapshot presence and explicit requirements. Each source unit maps
  exactly once to a row or explicit non-operation classification.
- Disposition records use one of `supported`, `coverage-child`, `proposed-exclusion`,
  or `unknown`; coverage children name existing issues from #620's decomposition.

Verification:

- Mode: focused-test. Complete source-topic and source-unit accounting is observed by running
  the validator with four explicit retained corpus directories; omission or content drift
  fails. Green: `uv run --no-sync python scripts/check_capability_inventory.py --source
  commands-p10=/home/dave/src/hmc-mcp/docs/refs/hmc-commands-p10 --source
  commands-p11=/home/dave/src/hmc-mcp/docs/refs/hmc-commands-p11 --source
  rest-p10=/home/dave/src/hmc-mcp/docs/refs/hmc-rest-api-p10 --source
  rest-p11=/home/dave/src/hmc-mcp/docs/refs/hmc-rest-api-p11` (the durable command records
  only logical source IDs; the local absolute paths remain private workflow evidence).
- Mode: focused-test. Every current operation ID is reconciled by importing the server's
  `TOOL_SECURITY` keys in the test and passing their `operation` values to the validator.
  Removing one join must fail; restore it and rerun the focused suite.
- Mode: task-test-not-applicable. Whether two IBM source spellings are semantically
  equivalent is an authored domain judgment with no independent executable oracle; review
  checks every multi-source equivalence explanation and preserves separate rows where the
  source shows distinct modes or constraints.

Steps:

1. Generate the corpus and source-unit manifest deterministically from YAML front matter,
   file bytes, command synopsis/option structures, REST resource/method/property/field
   structures, job names, and version/capability statements, including navigation documents
   and explicit unknown capture times.
2. Extract candidate CLI synopsis modes/options and REST resource/method/job rows, then
   manually review headings, nested attribute tables, version notes, and overview/schema
   classifications. Candidate extraction is temporary and is not committed as authority.
3. Normalize equivalent P10/P11 semantics only when both cited sources support the same
   mode and parameters; retain separate constraints and presence flags.
4. Map supported scopes to installable handler/CLI/MCP surfaces and existing tests. Map
   uncovered scopes to the concrete #620 child that owns them; proposed exclusions remain
   explicit and unknown questions remain visible.
5. Reconcile all distinct current `ToolSecurity.operation` values and discovered tool
   names/handlers/MCP surfaces. For every supported source parameter, record a real handler
   parameter, explicit constant/default, or cited translation path; explain composites whose
   operation combines several reference rows or is repository-specific. Record CLI exposure
   only where its registration is discovered and validated.
6. Run both focused commands and inspect the completeness summary and every unknown or
   proposed-exclusion row. Expect structural validity; do not claim complete coverage while
   either list is nonempty.
7. Commit as `docs: inventory HMC reference capabilities`.

Acceptance: all 826 captured pages and six navigation/overview files and every derived
source unit are accounted for; every row has mode/parameter detail and a disposition;
every registry operation joins with valid implementation evidence.

## Task 3: Make inventory drift a repository guardrail

This task ensures later registry and artifact changes cannot silently bypass reconciliation.

Interfaces:

- `just capability-inventory` runs the offline validator.
- `static` depends on `capability-inventory`.
- A prek hook with ID `capability-inventory`, `entry: just capability-inventory`,
  `language: system`, and `pass_filenames: false` mirrors the static member.
- `tests/test_ci_pipeline.py` includes the gate in its explicit required set.

Verification:

- Mode: focused-test. Gate/hook symmetry and required-gate retention are observed by
  `uv run --no-sync pytest -q tests/test_ci_pipeline.py --no-cov`; before wiring, the
  new required gate assertion fails. After wiring it passes.
- Mode: focused-test. The gate itself is observed by `just capability-inventory`; expect
  structural validity and an honest completeness line.

Steps:

1. Add `capability-inventory` to the required gate set and observe the focused failure.
2. Add the just recipe, static dependency, and matching unrestricted system hook.
3. Run both focused commands and expect success.
4. Run `just verify` and `uv run --no-sync prek run --all-files`; expect all checks pass.
5. Review the merge-base diff for generated noise, private identifiers, stale paths, and
   scope growth. Commit as `ci: guard capability inventory drift`.

Acceptance: local static checks, hooks, and CI all invoke the same canonical validator.

## Rollback and resume

Each task is one commit and can be reverted independently only after dependent later commits
are reverted. The ledger does not migrate runtime or user data. If source validation cannot
run, preserve the artifacts and report the exact missing retained input; never weaken hashes
or claim completeness. If a later review defers anything, record its owner here before forge.
