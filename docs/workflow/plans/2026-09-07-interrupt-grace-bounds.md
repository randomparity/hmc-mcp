# Implementation plan: interrupt grace bounds in `scripts/run_tests.py`

**Goal.** Stop `scripts/run_tests.py` truncating an interrupted pytest's `KeyboardInterrupt`
diagnostic on a loaded host, without letting a wedged child stall the job.

**Architecture.** `scripts/run_tests.py` runs pytest as a child whose output is captured to a
temporary file and replayed on any non-clean exit. One helper, `_settle_interrupted`, currently
spends a single constant on two waits with different jobs. This splits those into two constants
and two functions, and makes both waits treat a further `KeyboardInterrupt` as an instruction to
escalate. Design: `../specs/2026-09-07-interrupt-grace-bounds-design.md`. Decision:
`../../adr/0130-interrupt-diagnostic-window-and-reap.md`.

**Tech stack.** CPython, standard library only (`subprocess`, `os`, `shutil`, `sys`,
`tempfile`); pytest; `just` for guardrails.

## Global Constraints

- `requires-python = ">=3.11"` (`pyproject.toml:8`). CI runs eight legs: {amd64, arm64} x
  CPython {3.11, 3.12, 3.13, 3.14}. Nothing here may depend on a version above 3.11.
- No new dependency, and no new `static` sub-recipe (one would need a matching prek hook, which
  `tests/test_ci_pipeline.py` asserts 1:1).
- One test module per `scripts/` file: every test below goes in
  `tests/scripts/test_run_tests.py` and nowhere else.
- Guardrail: `just verify`. CI additionally runs `uv run --no-sync prek run --all-files`.
- Bootstrap only with `just setup`; never a bare `uv sync`, `uv run`, or `uv add`.
- `docs/adr/0130-*.md` is the assigned number; there is no ADR index to update. ADR 0129 is
  merged and append-only, is not superseded here, and must not be edited.

Expected implementation size: 90–120 changed lines (S) — from the file map below: about 30
changed lines in `scripts/run_tests.py`, about 75 in `tests/scripts/test_run_tests.py` (one
shared stub, one helper, three new tests, two edits), about 4 in `CHANGELOG.md`.

## File map

| File | Change | Answerable for |
|---|---|---|
| `scripts/run_tests.py` | modify | The two bounds, the two escalation functions, which arm calls which |
| `tests/scripts/test_run_tests.py` | modify | The shared interrupt stub, the budget helper, and every test below |
| `CHANGELOG.md` | modify | The `## [Unreleased]` entry |

`docs/adr/0130-interrupt-diagnostic-window-and-reap.md` and
`docs/workflow/specs/2026-09-07-interrupt-grace-bounds-design.md` are already written.

## Task 1 — Separate the diagnostic window from the reap

The whole change, and one task: no reviewer could accept the constants without the escalation
behaviour that makes the larger one affordable, or either without the tests.

### Interfaces

Consumed from `scripts/run_tests.py`, all confirmed present at `313256d2`: `CHUNK_SIZE`,
`TEST_TIMEOUT_SECONDS`, `_PYTEST_ENVIRONMENT_OVERRIDES`, `_replay(output: BinaryIO) -> None`,
`_exit_status(returncode: int) -> int`, `main() -> int`.

Provided to `tests/scripts/test_run_tests.py`: `run_tests.INTERRUPT_GRACE_SECONDS: int` (60),
`run_tests.TERMINATE_GRACE_SECONDS: int` (3),
`run_tests._settle_interrupted(process: subprocess.Popen[bytes]) -> None`, and
`run_tests._stop(process: subprocess.Popen[bytes]) -> None`.

Consumed by the test module from itself, all confirmed present at `313256d2`: `run_tests`
(line 21), `TrackingTemporaryFile` (line 25), `BinaryStderr` (line 50),
`_READINESS_TIMEOUT_SECONDS` (line 59), `_INTERRUPT_COLLECTION_SLACK_SECONDS` (line 63).
New in that module: `InterruptingProcess` and `_collection_budget() -> float`.

### Verification

- **The diagnostic window is at least the readiness ceiling and the reap is the smaller bound.**
  Mode: focused-test. Test: `test_diagnostic_window_covers_the_readiness_ceiling`. Red:
  `AttributeError: module 'run_tests' has no attribute 'TERMINATE_GRACE_SECONDS'`. Green:
  `uv run --no-sync pytest tests/scripts/test_run_tests.py -k diagnostic_window_covers`.
- **A further `KeyboardInterrupt` escalates instead of escaping `main`** — two cases: a second
  reaches `terminate()` and still replays the captured output; a third reaches `kill()`. Mode:
  focused-test. Test: `test_further_interrupts_escalate_without_escaping_main`, parametrized.
  Red: the `KeyboardInterrupt` propagates out of `main()`, so pytest reports an error rather
  than a return of `130`. Green: `... -k further_interrupts_escalate`.
- **The timeout arm escalates without paying the diagnostic window.** Mode: focused-test.
  Observable: the timeout values the stub's `wait` was called with. Test: the existing
  `test_timeout_terminates_pytest_and_returns_timeout_status`, extended. Red: `AttributeError`
  on the absent `TERMINATE_GRACE_SECONDS`. Green: `... -k timeout_terminates_pytest`.
- **The real-interrupt collection budget tracks both module bounds.** Mode: focused-test.
  Test: `test_collection_budget_tracks_both_module_bounds`. Red: `AttributeError` from
  `monkeypatch.setattr(run_tests, "TERMINATE_GRACE_SECONDS", 5)`, which refuses to set an
  attribute the module does not have. Green: `... -k collection_budget_tracks`.
- **ADR 0130's record form.** Mode: task-test-not-applicable. Changed surface:
  `docs/adr/0130-interrupt-diagnostic-window-and-reap.md`. Reason: `just adr-numbering`
  (`scripts/check_adr_numbering.py`) validates only an ADR's filename and H1 number, and no
  executable consumer reads a record's body, so no task-specific observation could fail
  meaningfully beyond that repository-wide gate.
- **The `CHANGELOG.md` entry.** Mode: task-test-not-applicable. Changed surface: the
  `## [Unreleased]` section. Reason: `tests/unit/test_changelog.py` enforces only the
  released-version and `hmc_mcp.api` facade rules; a prose entry for a dev-tooling script has no
  machine-checkable contract, and asserting its wording would be a prose snapshot.

### Steps

1. In `tests/scripts/test_run_tests.py`, after `BinaryStderr` (ends line 54), add the shared
   stub:

   ```python
   class InterruptingProcess:
       """A child whose first `interrupts` waits raise `KeyboardInterrupt`.

       One stub covers the whole escalation ladder: one interrupt is the ordinary
       Ctrl-C, two reach `terminate()`, three reach `kill()`. `payload` is written
       to `capture` on the first wait, standing in for what pytest managed to emit.
       """

       returncode = 2

       def __init__(self, interrupts: int, capture: BinaryIO, payload: bytes) -> None:
           self.interrupts = interrupts
           self.capture = capture
           self.payload = payload
           self.wait_count = 0
           self.terminated = False
           self.killed = False

       def wait(self, timeout: float | None = None) -> int:
           self.wait_count += 1
           if self.wait_count == 1:
               self.capture.write(self.payload)
           if self.wait_count <= self.interrupts:
               raise KeyboardInterrupt
           return self.returncode

       def terminate(self) -> None:
           self.terminated = True

       def kill(self) -> None:
           self.killed = True
   ```

   Add `BinaryIO` to the `typing` import on line 13.

2. After `_INTERRUPT_COLLECTION_SLACK_SECONDS` (line 63), add the budget helper:

   ```python
   def _collection_budget() -> float:
       """The bounded portion of `run_tests._settle_interrupted`, plus slack.

       Both bounds are read from the module under test, so a change to either
       moves this budget with it (ADR 0129, extended by ADR 0130).
       """
       return (
           run_tests.INTERRUPT_GRACE_SECONDS
           + run_tests.TERMINATE_GRACE_SECONDS
           + _INTERRUPT_COLLECTION_SLACK_SECONDS
       )
   ```

3. Rewrite `test_interruption_replays_captured_output_without_traceback` (line 228) to use the
   shared stub in place of its local `InterruptedProcess`, keeping its existing assertions:
   construct `InterruptingProcess(1, temporary_file, output)` and drop the local class.

4. Add the three new tests after that one:

   ```python
   def test_diagnostic_window_covers_the_readiness_ceiling() -> None:
       """ADR 0130: no host clearing the readiness ceiling may lose the diagnostic."""
       assert run_tests.INTERRUPT_GRACE_SECONDS >= _READINESS_TIMEOUT_SECONDS
       assert run_tests.TERMINATE_GRACE_SECONDS < run_tests.INTERRUPT_GRACE_SECONDS


   def test_collection_budget_tracks_both_module_bounds(
       monkeypatch: pytest.MonkeyPatch,
   ) -> None:
       """A budget frozen to a literal would stop tracking the code under test."""
       monkeypatch.setattr(run_tests, "INTERRUPT_GRACE_SECONDS", 11)
       monkeypatch.setattr(run_tests, "TERMINATE_GRACE_SECONDS", 5)

       assert _collection_budget() == 16 + _INTERRUPT_COLLECTION_SLACK_SECONDS


   @pytest.mark.parametrize(("interrupts", "killed"), [(2, False), (3, True)])
   def test_further_interrupts_escalate_without_escaping_main(
       monkeypatch: pytest.MonkeyPatch, interrupts: int, killed: bool
   ) -> None:
       """A further Ctrl-C escalates; it must not escape `main` and strand the child."""
       output = b"pytest report before the next interrupt \xff\n"
       stderr = BinaryStderr()
       temporary_file = TrackingTemporaryFile()
       process = InterruptingProcess(interrupts, temporary_file, output)
       monkeypatch.setattr(run_tests.sys, "stderr", stderr)
       monkeypatch.setattr(run_tests.tempfile, "TemporaryFile", lambda: temporary_file)
       monkeypatch.setattr(
           run_tests.subprocess, "Popen", lambda _command, **_kwargs: process
       )

       assert run_tests.main() == 130

       assert process.terminated
       assert process.killed is killed
       assert stderr.buffer.getvalue() == output
       assert temporary_file.closed
   ```

5. Extend `test_timeout_terminates_pytest_and_returns_timeout_status` (line 258): give its
   `TimedOutProcess` an `__init__` setting `self.wait_count = 0` and
   `self.timeouts: list[float | None] = []` in place of the `wait_count` class attribute, append
   `timeout` to `self.timeouts` at the top of `wait`, and add after the existing
   `assert process.wait_count == 2`:

   ```python
       # The timeout arm escalates straight away: nothing has asked this child to
       # stop, so a diagnostic window would only delay the report (ADR 0130).
       assert process.timeouts == [
           run_tests.TEST_TIMEOUT_SECONDS,
           run_tests.TERMINATE_GRACE_SECONDS,
       ]
   ```

6. Point the real-interrupt test at the helper. In
   `test_real_interrupt_preserves_pytest_diagnostic`, replace lines 362-363
   (`grace = ...` and `budget = 2 * grace + ...`) with `budget = _collection_budget()`, and in
   both failure messages replace the `{grace}` reference with
   `{run_tests.INTERRUPT_GRACE_SECONDS}` labelled `window` and
   `{run_tests.TERMINATE_GRACE_SECONDS}` labelled `reap`, leaving the readiness, settle, and
   stderr-tail parts of both messages as they are.

7. Confirm the expected red: `uv run --no-sync pytest tests/scripts/test_run_tests.py` fails
   exactly the four tests in the Verification inventory plus
   `test_real_interrupt_preserves_pytest_diagnostic`, which step 6 has just pointed at
   `_collection_budget()`. No other test may fail.

8. In `scripts/run_tests.py`, replace line 11 with:

   ```python
   INTERRUPT_GRACE_SECONDS = 60
   TERMINATE_GRACE_SECONDS = 3
   ```

9. Replace `_settle_interrupted` (lines 21-31) with two functions:

   ```python
   def _stop(process: subprocess.Popen[bytes]) -> None:
       """Stop a child that will not exit on its own, escalating to SIGKILL.

       A `KeyboardInterrupt` here is a further Ctrl-C asking to stop now, so it
       escalates exactly as an expired wait does.
       """
       process.terminate()
       try:
           process.wait(timeout=TERMINATE_GRACE_SECONDS)
       except (subprocess.TimeoutExpired, KeyboardInterrupt):
           process.kill()
           process.wait()


   def _settle_interrupted(process: subprocess.Popen[bytes]) -> None:
       """Give an interrupted pytest time to emit diagnostics, then stop it.

       The window is a ceiling, not a latency budget: the wait returns the moment
       the child exits, so only a child that ignores `SIGINT` pays it. ADR 0130
       sizes it against the readiness ceiling ADR 0129 set.
       """
       try:
           process.wait(timeout=INTERRUPT_GRACE_SECONDS)
       except (subprocess.TimeoutExpired, KeyboardInterrupt):
           _stop(process)
   ```

10. In `main`, point the timeout arm at `_stop`, leaving the interrupt arm unchanged:

    ```python
            except subprocess.TimeoutExpired:
                timed_out = True
                _stop(process)
            except KeyboardInterrupt:
                interrupted = True
                _settle_interrupted(process)
    ```

11. Confirm green: `uv run --no-sync pytest tests/scripts/test_run_tests.py` passes, including
    `test_real_interrupt_preserves_pytest_diagnostic`.

12. Add the `CHANGELOG.md` entry under `## [Unreleased]`, in the `### Changed` subsection
    (creating it if absent), matching neighbouring entries' voice:

    ```markdown
    - `scripts/run_tests.py` no longer truncates an interrupted pytest's `KeyboardInterrupt`
      diagnostic on a loaded host: the window before `SIGTERM` and the reap before `SIGKILL`
      are separate bounds, and a second `Ctrl-C` escalates immediately rather than escaping
      with the captured output unreplayed and the child orphaned (ADR 0130).
    ```

13. Run the guardrails bare, in the foreground: `just verify`, then
    `uv run --no-sync prek run --all-files`. Both must exit 0.

14. Commit. Conventional commits 1.0.0, imperative, subject at most 72 characters.

### Acceptance criteria

- `scripts/run_tests.py` defines `INTERRUPT_GRACE_SECONDS = 60` and
  `TERMINATE_GRACE_SECONDS = 3`, and neither wait uses the other's bound.
- `_settle_interrupted` is reached only from the `KeyboardInterrupt` arm; the
  `subprocess.TimeoutExpired` arm calls `_stop` directly.
- Both waits catch `KeyboardInterrupt` alongside `subprocess.TimeoutExpired`.
- Every verification entry above is green and the rest of `tests/scripts/test_run_tests.py`
  still passes with its assertions unchanged in intent.
- `just verify` and `uv run --no-sync prek run --all-files` both exit 0.
- `CHANGELOG.md` has the `## [Unreleased]` entry; `docs/adr/0129-*.md` is unmodified.

### Rollback

Confined to the three files in the file map, with no dependency, migration, or persisted state.
Reverting the commit restores the previous behaviour exactly.
