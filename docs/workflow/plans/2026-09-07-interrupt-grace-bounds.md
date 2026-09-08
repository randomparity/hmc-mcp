# Implementation plan: interrupt grace bounds in `scripts/run_tests.py`

**Goal.** Stop `scripts/run_tests.py` truncating an interrupted pytest's `KeyboardInterrupt`
diagnostic on a loaded host, without letting a wedged child stall the job. `_settle_interrupted`
spends one constant on two waits with different jobs; this splits them into two constants and two
functions, both waits treating a further `KeyboardInterrupt` as an instruction to escalate.
Problem and success criteria: `../specs/2026-09-07-interrupt-grace-bounds-design.md`. Decision
and measurements: `../../adr/0130-interrupt-diagnostic-window-and-reap.md`. Stack: CPython,
standard library only; pytest; `just` for guardrails.

## Global Constraints

- `requires-python = ">=3.11"` (`pyproject.toml:8`); CI runs eight legs, {amd64, arm64} x
  CPython {3.11, 3.12, 3.13, 3.14}, so nothing here may depend on a version above 3.11.
- No new dependency, and no new `static` sub-recipe (one would need a matching prek hook, which
  `tests/test_ci_pipeline.py` asserts 1:1). One test module per `scripts/` file: every test below
  goes in `tests/scripts/test_run_tests.py`.
- Guardrail `just verify`; CI also runs `uv run --no-sync prek run --all-files`. Bootstrap only
  with `just setup`. ADR 0129 is merged, append-only, not superseded here and must not be edited;
  there is no ADR index.

Expected implementation size: 85–115 changed lines (S) — from the file map: about 30 in
`scripts/run_tests.py`, about 70 in `tests/scripts/test_run_tests.py` (a shared stub replacing a
local one, two new tests, two edits), about 4 in `CHANGELOG.md`.

## File map

| File | Change | Answerable for |
|---|---|---|
| `scripts/run_tests.py` | modify | The two bounds, the two escalation functions, which arm calls which |
| `tests/scripts/test_run_tests.py` | modify | The shared interrupt stub and every test below |
| `CHANGELOG.md` | modify | The `## [Unreleased]` entry |

## Task 1 — Separate the diagnostic window from the reap

One task: the constants are unacceptable without the escalation behaviour that makes the larger
one affordable, and neither is acceptable without the tests.

### Interfaces

Consumed, all confirmed present at `313256d2`: `TEST_TIMEOUT_SECONDS`,
`_replay(output: BinaryIO) -> None`, `main() -> int` in `scripts/run_tests.py`; `run_tests`,
`TrackingTemporaryFile`, `BinaryStderr`, `_READINESS_TIMEOUT_SECONDS` and
`_INTERRUPT_COLLECTION_SLACK_SECONDS` in the test module (lines 21, 25, 50, 59, 63).
Provided: `run_tests.INTERRUPT_GRACE_SECONDS: int` (60),
`run_tests.TERMINATE_GRACE_SECONDS: int` (3),
`run_tests._stop(process: subprocess.Popen[bytes]) -> None`, the unchanged signature of
`_settle_interrupted(process: subprocess.Popen[bytes]) -> None`, and `InterruptingProcess`.

### Verification

Green command: `uv run --no-sync pytest tests/scripts/test_run_tests.py -k <selector>`.

- **The window is at least the readiness ceiling, and the reap is the smaller bound.** Mode:
  focused-test. Test: `test_diagnostic_window_covers_the_readiness_ceiling`. Red:
  `AttributeError: module 'run_tests' has no attribute 'TERMINATE_GRACE_SECONDS'`. Selector:
  `diagnostic_window_covers`.
- **A further `KeyboardInterrupt` escalates instead of escaping `main`** — a second reaches
  `terminate()` with the captured output still replayed and status `130`; a third reaches
  `kill()`. Mode: focused-test. Test:
  `test_further_interrupts_escalate_without_escaping_main`, parametrized over both. Red: the
  `KeyboardInterrupt` propagates out of `main()`, so pytest reports an error, not `130`.
  Selector: `further_interrupts_escalate`.
- **The timeout arm escalates without paying the window.** Mode: focused-test. Observable: the
  timeout values the stub's `wait` received. Test: the existing
  `test_timeout_terminates_pytest_and_returns_timeout_status`, extended. Red: `AttributeError`
  on the absent `TERMINATE_GRACE_SECONDS`. Selector: `timeout_terminates_pytest`.
- **The `CHANGELOG.md` entry.** Mode: task-test-not-applicable. Changed surface: the
  `## [Unreleased]` section. Reason: `tests/unit/test_changelog.py` enforces only the
  released-version and `hmc_mcp.api` facade rules; a prose entry for a dev-tooling script has no
  machine-checkable contract, and asserting its wording would be a prose snapshot.

### Steps

Code is specified rather than quoted throughout: every constant, signature, branch and assertion
is named, and ADR 0130 fixes the behaviour behind them.

1. In `tests/scripts/test_run_tests.py`, add `BinaryIO` to the `typing` import (line 13) and a
   module-level stub `InterruptingProcess` after `BinaryStderr` (ends line 54), replacing the
   local `InterruptedProcess` and covering the ladder — one interrupt is the ordinary Ctrl-C, two
   reach `terminate()`, three reach `kill()`.
   `__init__(self, interrupts: int, capture: BinaryIO, payload: bytes) -> None` stores those and
   sets `wait_count = 0`, `terminated = False`, `killed = False`; class attribute
   `returncode = 2`; `wait(self, timeout: float | None = None) -> int` increments `wait_count`,
   writes `payload` to `capture` on the first call, raises `KeyboardInterrupt` while
   `wait_count <= interrupts`, else returns `returncode`; `terminate()` and `kill()` set their
   flags.

2. Rewrite `test_interruption_replays_captured_output_without_traceback` (line 228) to build
   `InterruptingProcess(1, temporary_file, output)` and drop its local class, keeping every
   existing assertion in that test unchanged.

3. Add `test_diagnostic_window_covers_the_readiness_ceiling()`, taking no fixtures, asserting
   `run_tests.INTERRUPT_GRACE_SECONDS >= _READINESS_TIMEOUT_SECONDS` and
   `run_tests.TERMINATE_GRACE_SECONDS < run_tests.INTERRUPT_GRACE_SECONDS`, with a docstring
   naming ADR 0130 as why the window may not drop below the ceiling.

4. Add `test_further_interrupts_escalate_without_escaping_main`, decorated
   `@pytest.mark.parametrize(("interrupts", "killed"), [(2, False), (3, True)])`, taking
   `monkeypatch: pytest.MonkeyPatch, interrupts: int, killed: bool`. It builds a `BinaryStderr`,
   a `TrackingTemporaryFile`, and `InterruptingProcess(interrupts, temporary_file, payload)` for
   a payload containing a non-UTF-8 byte; monkeypatches `run_tests.sys.stderr`,
   `run_tests.tempfile.TemporaryFile` and `run_tests.subprocess.Popen` exactly as
   `test_interruption_replays_captured_output_without_traceback` does; asserts
   `run_tests.main() == 130`, `process.terminated`, `process.killed is killed`,
   `stderr.buffer.getvalue() == payload`, `temporary_file.closed`.

5. Extend `test_timeout_terminates_pytest_and_returns_timeout_status` (line 258): replace the
   `TimedOutProcess.wait_count` class attribute with an `__init__` setting `self.wait_count = 0`
   and `self.timeouts: list[float | None] = []`, append `timeout` to `self.timeouts` at the top
   of `wait`, and after the existing `assert process.wait_count == 2` assert
   `process.timeouts == [run_tests.TEST_TIMEOUT_SECONDS, run_tests.TERMINATE_GRACE_SECONDS]`,
   with a comment recording why the timeout arm skips the window (ADR 0130).

6. In `test_real_interrupt_preserves_pytest_diagnostic`, replace lines 362-363
   (`grace = ...` and `budget = 2 * grace + ...`) with a budget summing
   `run_tests.INTERRUPT_GRACE_SECONDS`, `run_tests.TERMINATE_GRACE_SECONDS` and
   `_INTERRUPT_COLLECTION_SLACK_SECONDS`, keeping ADR 0129's rule that it reads its bounds from
   the module under test. In both failure messages replace the single `{grace}s` reference with
   the window and the reap, labelled `window` and `reap`, leaving the readiness, settle-interval
   and stderr-tail parts as they are.

7. Confirm the expected red: `uv run --no-sync pytest tests/scripts/test_run_tests.py` fails
   exactly the three tests in the Verification inventory plus
   `test_real_interrupt_preserves_pytest_diagnostic`, which step 6 has just pointed at the
   absent `TERMINATE_GRACE_SECONDS`. No other test may fail.

8. In `scripts/run_tests.py`, replace line 11's single constant with
   `INTERRUPT_GRACE_SECONDS = 60` and `TERMINATE_GRACE_SECONDS = 3`.

9. Replace `_settle_interrupted` (lines 21-31) with two functions.
   `_stop(process: subprocess.Popen[bytes]) -> None` calls `process.terminate()`, then
   `process.wait(timeout=TERMINATE_GRACE_SECONDS)` inside
   `try: ... except (subprocess.TimeoutExpired, KeyboardInterrupt):` whose handler calls
   `process.kill()` then a bare `process.wait()`; its docstring says it stops a child that will
   not exit on its own, escalating to SIGKILL, and that a `KeyboardInterrupt` here is a further
   Ctrl-C asking to stop now, so it escalates as an expired wait does.
   `_settle_interrupted(process: subprocess.Popen[bytes]) -> None` keeps its signature and calls
   `process.wait(timeout=INTERRUPT_GRACE_SECONDS)` under the same two-exception `except`, whose
   handler calls `_stop(process)`; its docstring keeps the existing summary line and adds that
   the window is a ceiling rather than a latency budget — the wait returns the moment the child
   exits, so only a child ignoring `SIGINT` pays it (ADR 0130).

10. In `main`, point the timeout arm at `_stop` (line 58), leaving the `KeyboardInterrupt` arm on
    `_settle_interrupted`.

11. Confirm green: `uv run --no-sync pytest tests/scripts/test_run_tests.py` passes, including
    `test_real_interrupt_preserves_pytest_diagnostic`.

12. Add the `CHANGELOG.md` entry under `## [Unreleased]`, in its `### Changed` subsection
    (creating it if absent), matching neighbouring entries' voice: `scripts/run_tests.py` no
    longer truncates an interrupted pytest's `KeyboardInterrupt` diagnostic on a loaded host, the
    window before `SIGTERM` and the reap before `SIGKILL` being separate bounds, and a second
    `Ctrl-C` escalating at once rather than escaping with the output unreplayed and the child
    orphaned (ADR 0130). Then run the guardrails bare and in the foreground — `just verify`, then
    `uv run --no-sync prek run --all-files`, both exiting 0 — and commit with a Conventional
    Commits 1.0.0 subject of at most 72 characters.

### Acceptance criteria

Every Verification entry green; the rest of `tests/scripts/test_run_tests.py` still passing with
its assertions unchanged in intent; `_settle_interrupted` reached only from the
`KeyboardInterrupt` arm and `_stop` called directly by the `subprocess.TimeoutExpired` arm; both
waits catching `KeyboardInterrupt`; both guardrail commands exit 0; `docs/adr/0129-*.md`
unmodified. Rollback is a revert of the one commit.
