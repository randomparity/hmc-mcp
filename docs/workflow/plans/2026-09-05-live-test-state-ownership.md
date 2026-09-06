# Implement explicit live-test configuration and artifact ownership

Goal: Replace the mixed live-test context with immutable configuration and mutable run
artifacts while preserving scenario behavior.

Architecture: `LiveTestConfig` owns parsed operator input and is frozen.
`LiveTestArtifacts` owns invocation discoveries and recovery state. `RunState` exposes
both explicitly, and result documents persist and restore them separately.

Tech stack: Python 3.11+, stdlib dataclasses and JSON, pytest, ruff, ty.

Expected implementation size: 1,500–1,800 changed lines (L) — corrected from the
initial 260–420 estimate after the in-flight implementation measured 1,694 changed
lines, including 1,298 ownership-access diff lines across roughly 645 affected source
and test lines, plus the state and persistence changes.

## Global Constraints

- Python versions: 3.11, 3.12, 3.13, and 3.14.
- Architectures: amd64 and arm64; the current x86_64 host covers amd64 locally.
- Add no dependency and change no production package contract.
- Preserve every `LIVE_TEST_*` key and validation rule.
- Preserve scenario ordering, cleanup guarantees, redaction, and live operation behavior.
- Follow ADR 0124.
- Base branch: `main`; branch: `feat/separate-live-test-state-700`.
- Guardrails: `just test`, `just smoke`, `just verify`, and
  `uv run --no-sync prek run --all-files`.

## Task 1: Split the state model and persistence contract

Files: modify `scripts/live_test_runner.py` and `tests/test_live_runner.py`.

Interfaces:

- Define `@dataclass(frozen=True) class LiveTestConfig` with
  `from_env_file(cls, path: Path | None = None) -> LiveTestConfig` and the current
  configured fields and ISO properties.
- Define `@dataclass class LiveTestArtifacts` with every discovered UUID, baseline,
  virtual-media flag, and restored runtime value.
- Define `RunState.config: LiveTestConfig` and
  `RunState.artifacts: LiveTestArtifacts` using default factories.
- Persist top-level `config`, `hmc`, `artifacts`, and `results` members. `results`
  preserves `state.results` unchanged. `hmc` contains `host`, `port`, `user`, and
  `verify_ssl` from the post-bootstrap `HMCConfig`; restoration receives that current
  config and installs artifacts only after all comparisons and decoding succeed.
- Decode saved config through its declared schema so JSON arrays such as
  `protected_lpar_names` become their in-memory tuple form before equality comparison.
- Decode artifacts with explicit nullable-string, integer-not-boolean, boolean, mapping,
  and list-of-string field categories into a fresh candidate.

Verification:

- Mode: focused-test — immutable configuration; add
  `test_live_config_is_frozen`, observe assignment succeeds before the split, then run
  `uv run --no-sync pytest tests/test_live_runner.py -q` and expect it to pass after
  implementation.
- Mode: focused-test — atomic artifact restoration and compatibility checks; replace the
  context restoration cases with a real JSON round-trip success containing
  `protected_lpar_names`, per-field HMC identity mismatch, config mismatch, unknown-key,
  wrong-type (including bool-as-int), legacy-shape, and no-partial-update cases; the same
  focused command must pass.
- Mode: focused-test — persisted scenario evidence; assert a written report retains the
  accumulated `state.results` rows under its top-level `results` member; the same focused
  command must pass.

Steps:

1. Add the focused tests importing `FrozenInstanceError`, construct
   `LiveTestConfig`, attempt `config.system_name = "changed"`, and assert the exception;
   write separated JSON fixtures through `json.dumps`/`json.loads`, pass a current
   `HMCConfig.from_mapping(...)`, and assert only artifact fields restore.
2. Run `uv run --no-sync pytest tests/test_live_runner.py -q`; expect failures naming
   missing `LiveTestConfig`, `LiveTestArtifacts`, or separated document members.
3. Move configured fields, `_CONFIG_FIELDS`, parsing, validation, and ISO properties into
   frozen `LiveTestConfig`; move mutable fields into `LiveTestArtifacts`; replace
   `RunState.context` with explicit members.
4. Update result serialization to write `asdict(state.config)`, the four-field non-secret
   HMC identity, `asdict(state.artifacts)`, and `state.results`. Add explicit config and
   artifact decoders; reject missing or extra restoration members, unknown keys, wrong
   JSON types, config/identity mismatches, and the old `context` shape before assigning
   the fresh candidate. Keep result rows opaque to restoration.
5. Run `uv run --no-sync pytest tests/test_live_runner.py -q`; expect all tests green.
6. Commit with `refactor: separate live-test state ownership`.

Acceptance: configuration cannot mutate; artifacts remain mutable and invocation-local;
valid new result files restore atomically; stale, mismatched, malformed, and legacy files
cannot change state.

Rollback: revert the task commit; no persisted compatibility promise requires migration.

## Task 2: Migrate scenarios to explicit ownership

Files: modify scenario modules under `scripts/live_test/` and their coupled assertions in
`tests/test_live_runner.py`.

Interfaces:

- Scenario functions continue accepting `(client, state: RunState)`.
- Operator values are read through `state.config`.
- Discovered values and mutable collections are read or changed through
  `state.artifacts`.

Verification:

- Mode: focused-test — scenario behavior and cleanup are unchanged while ownership is
  explicit; migrate existing live-runner tests and run
  `uv run --no-sync pytest tests/test_live_runner.py -q`, expecting all tests green.

Steps:

1. For every scenario reference, classify the field against the two dataclasses and
   replace `state.context` with `state.config` or `state.artifacts`; use local variable
   names `config` and `artifacts` where a function needs both.
2. Migrate helpers and test setup/assertions to the explicit members without introducing
   a compatibility facade.
3. Run `rg -n "LiveTestContext|state\\.context|\\bcontext\\." scripts/live_test_runner.py scripts/live_test tests/test_live_runner.py`; expect no ownership-model references (ordinary function parameters named for unrelated contexts may remain only when inspected and justified).
4. Run `uv run --no-sync pytest tests/test_live_runner.py -q`; expect all tests green.
5. Run `just test` and `just smoke`; expect successful summaries.
6. Commit with `refactor: migrate live scenarios to explicit state`.

Acceptance: scenario calls, ordering, cleanup, and persisted recovery retain their current
behavior; every configured/discovered access declares its owner in code.

Rollback: revert this commit together with Task 1 because the new interfaces intentionally
replace the old surface.

## Task 3: Verify the complete repository contract

Files: modify generated artifacts only when their named generator reports staleness.

Interfaces: no new code interface; this task proves the branch integrates with repository
guardrails on the declared local host/target relationship.

Verification:

- Mode: focused-test — complete branch integration; run `just verify` and
  `uv run --no-sync prek run --all-files`, expecting exit 0 from both.

Steps:

1. Run `just verify`; if a generated-document gate fails, run its named generator and
   commit only the generated output; otherwise diagnose any current failure before edits.
2. Run `uv run --no-sync prek run --all-files`; expect every hook to pass.
3. Inspect `git diff "$(git merge-base HEAD origin/main)"` for scope, naming, and accidental
   compatibility aliases; expect only the approved files and behavior.

Acceptance: all local guardrails pass with no warnings and the diff remains within issue
#700's charter.

Rollback: revert the implementation commits; design records remain historical evidence.
