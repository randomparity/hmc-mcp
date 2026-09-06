# Separate live-test configuration from run artifacts

Implement issue #700 by replacing the mixed live-test context with explicit
immutable configuration and mutable execution artifacts. The runner remains a
standard-library Python program; scenario functions keep accepting `RunState`.

## Global Constraints

- Keep `HMC_*` connection precedence and semantics unchanged; local `.env` is authoritative for `LIVE_TEST_*` even when conflicting values are exported.
- Do not add dependencies.
- Missing or malformed `LIVE_TEST_*` values must fail before MCP client creation.
- `.env.example` uses only fictional, mutually consistent sample values.
- The pre-release ownership change removes `context` and legacy result-file support; add no compatibility alias or migration layer.

Expected implementation size: 260–380 changed lines (M) — split two data models, route scenario access, replace result persistence, and update focused tests.

## File map

- `scripts/live_test_runner.py`: owns the two types, config parsing, result envelope, and restoration.
- `scripts/live_test/*.py`: reads immutable inputs from `state.config` and discoveries from `state.artifacts`.
- `tests/test_live_runner.py`: proves ownership, persistence, restoration, and scenario integration.

## Task 1: Define explicit ownership

Files: modify `scripts/live_test_runner.py`; modify `tests/test_live_runner.py`.

Interfaces: `LiveTestConfig.from_env_file(path: Path | None = None) -> LiveTestConfig`; `LiveTestArtifacts()`; `RunState(config: LiveTestConfig = ..., artifacts: LiveTestArtifacts = ...)`.

Verification:

- Contract: parsed input cannot be assigned after construction. Mode: focused-test. Add `test_live_config_is_immutable`; it fails before the frozen type and passes with `uv run --no-sync pytest tests/test_live_runner.py -q`.
- Contract: every prior configured field belongs only to `LiveTestConfig`, while every discovered/mutated field belongs only to `LiveTestArtifacts`. Mode: focused-test. Add a field-set assertion and run the same command.

Steps:

1. Move all parser fields and ISO-derived properties into `@dataclass(frozen=True) class LiveTestConfig`; retain `from_env_file` and validation semantics.
2. Define `LiveTestArtifacts` with the UUID, baseline, network, volume, vMedia, and boot-order fields currently mutated by runner or scenarios.
3. Replace `RunState.context` with explicit `config` and `artifacts`, then add the two ownership tests and run the focused suite.

Acceptance: no production access to `state.context` remains, and assignment to configuration raises `FrozenInstanceError`.

## Task 2: Route scenarios and persist artifacts

Files: modify `scripts/live_test_runner.py`; modify `scripts/live_test/*.py`; modify `tests/test_live_runner.py`.

Interfaces: `_restore_artifacts_from_results(state: RunState, results_path: str) -> None`; results document has `config`, `artifacts`, and `results` members.

Verification:

- Contract: one emitted result document keeps config and artifacts in distinct objects. Mode: focused-test. Add an async runner test; it fails while `context` is emitted and passes with `uv run --no-sync pytest tests/test_live_runner.py -q`.
- Contract: restoration modifies only declared artifacts and rejects legacy or malformed documents without mutation. Mode: focused-test. Add restoration cases; they fail before the new reader and pass with the same command.
- Contract: a selected run aborts before scenario dispatch when saved config differs or artifact decoding is malformed, leaving its existing artifacts unchanged. Mode: focused-test. Add mismatch and partial-decode cases; they fail before the guard and pass with the same command.

Steps:

1. Replace scenario reads with `state.config` and mutations with `state.artifacts`; keep local aliases only when they name the explicit owner.
2. Serialize `asdict(state.config)` and `asdict(state.artifacts)` under their envelope keys.
3. Replace `_restore_ctx_from_results` with a declared-artifact-field reader; require saved config to equal `asdict(state.config)`, decode all artifact fields into a temporary value, and replace `state.artifacts` only on complete success. Reject missing or non-object members, including legacy `context` envelopes.
4. Make restoration failure return a pre-scenario failure for a selected run; do not dispatch a scenario with blank or partially restored state.
5. Add focused tests for emitted shape, artifact restoration, config non-mutation, legacy rejection, config mismatch, atomic malformed decode, and no scenario dispatch; run the focused suite.

Acceptance: restoration contains no config special cases, cannot mutate `state.config`, cannot partially mutate artifacts, and cannot dispatch a selected scenario after identity or decode failure.

## Final verification

Run `just lint`, `just typecheck`, `uv run --no-sync pytest tests/test_live_runner.py -q`, `just verify`, and `uv run --no-sync prek run --all-files`. The live hardware suite is excluded because it requires an operator-controlled environment.
