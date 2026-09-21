# Implementation plan: interrupt grace bounds in `scripts/run_tests.py`

**Goal.** Stop `scripts/run_tests.py` truncating an interrupted pytest's `KeyboardInterrupt`
diagnostic on a loaded host, without letting a wedged child stall the job. `_settle_interrupted`
spends one constant on two waits with different jobs; this splits them into two constants and two
functions, every wait treating a further `KeyboardInterrupt` as an instruction to escalate. Spec:
`../specs/2026-09-07-interrupt-grace-bounds-design.md`; decision and measurements:
`../../adr/0130-interrupt-diagnostic-window-and-reap.md`. CPython and the standard library only.

## Global Constraints

- `requires-python = ">=3.11"` (`pyproject.toml:8`); CI runs eight legs, {amd64, arm64} x CPython
  {3.11, 3.12, 3.13, 3.14}, so nothing may depend on a version above 3.11. No new dependency, no
  new import in `scripts/run_tests.py`, and no new `static` sub-recipe (one needs a matching prek
  hook, which `tests/test_ci_pipeline.py` asserts 1:1). One test module per `scripts/` file: every
  test below goes in `tests/scripts/test_run_tests.py`.
- `addopts` carries `--cov=hmc_mcp` with `fail_under = 90.5` (`pyproject.toml:98-106`), so **every
  focused pytest run needs `--no-cov` or it exits 1 on the package-wide gate** — the convention
  `pyproject.toml:97-98` documents. `just verify` keeps the gate.
- Guardrail `just verify`; CI also runs `uv run --no-sync prek run --all-files`. Bootstrap only
  with `just setup`. ADR 0129 is merged, append-only, not superseded, must not be edited; no index.

Expected implementation size: 100–135 changed lines (S) — from the file map: about 32 in
`scripts/run_tests.py`, about 95 in the test module (a shared stub replacing a local one, three
new tests, four edits), about 4 in `CHANGELOG.md`.

## File map

| File | Change | Answerable for |
|---|---|---|
| `scripts/run_tests.py` | modify | The two bounds, the two escalation functions, which arm calls which |
| `tests/scripts/test_run_tests.py` | modify | The shared stub, every test below, two stale clamp comments |
| `CHANGELOG.md` | modify | The `## [Unreleased]` entry |

## Task 1 — Separate the diagnostic window from the reap

One task: the constants are unacceptable without the escalation behaviour that makes the larger
one affordable, and neither is acceptable without the tests.

### Interfaces

Consumed, all confirmed present at `313256d2`: `TEST_TIMEOUT_SECONDS`,
`_replay(output: BinaryIO) -> None`, `main() -> int` in `scripts/run_tests.py`; `run_tests`,
`TrackingTemporaryFile`, `BinaryStderr`, `_READINESS_TIMEOUT_SECONDS`,
`_INTERRUPT_COLLECTION_SLACK_SECONDS` in the test module (lines 21, 25, 50, 59, 63). Provided:
`run_tests.INTERRUPT_GRACE_SECONDS: int` (300), `run_tests.TERMINATE_GRACE_SECONDS: int` (3),
`run_tests._stop(process: subprocess.Popen[bytes]) -> None`, the unchanged signature of
`_settle_interrupted`, and `InterruptingProcess`.

### Verification

Green command: `uv run --no-sync pytest tests/scripts/test_run_tests.py --no-cov -k <selector>`.

- **The reap is the smaller of the two bounds.** Mode: focused-test. Test:
  `test_the_reap_is_the_smaller_of_the_two_bounds`. Red: `AttributeError: module 'run_tests' has
  no attribute 'TERMINATE_GRACE_SECONDS'`. Selector: `reap_is_the_smaller`. The window's size
  answers to the repository suite measured in ADR 0130, which no test here can observe, so only
  the ladder's ordering is asserted.
- **A further `KeyboardInterrupt` escalates instead of escaping `main`** — a second reaches
  `terminate()` with the output still replayed and status `130`, a third reaches `kill()`. Mode:
  focused-test. Test: `test_further_interrupts_escalate_without_escaping_main`, parametrized over
  both. Red: the `KeyboardInterrupt` propagates out of `main()`, so pytest reports an error, not
  `130`. Selector: `further_interrupts_escalate`.
- **A wedged child is escalated through both rungs on the interrupt path** — completion criterion
  2's proof, and the only test driving the window's and the reap's *timeout* branches rather than
  their interrupt branches. Mode: focused-test. Test:
  `test_a_wedged_child_is_terminated_then_killed`. Red: `AttributeError: module 'run_tests' has no
  attribute 'TERMINATE_GRACE_SECONDS'`. Selector: `wedged_child_is_terminated`.
- **The timeout arm escalates without paying the window.** Mode: focused-test. Observable: the
  timeouts the stub's `wait` received. Test: the existing
  `test_timeout_terminates_pytest_and_returns_timeout_status`, extended. Red: the same
  `AttributeError`. Selector: `timeout_terminates_pytest`.
- **No orphaned child on the second-interrupt path.** Mode: task-test-not-applicable. Changed
  surface: `_stop`'s `terminate()`/`kill()` calls. Reason: the stubs prove `_stop` is reached and
  both rungs called, which is the routing this change alters; that those calls reap a real process
  is `subprocess.Popen` behaviour, and re-proving it needs a second `SIGINT` landing inside a
  window whose width depends on host load — a race with no deterministic in-process trigger.
- **The `CHANGELOG.md` entry.** Mode: task-test-not-applicable. Changed surface: the
  `## [Unreleased]` section. Reason: `tests/unit/test_changelog.py` holds one test,
  `test_declared_version_has_a_changelog_entry`, checking only that the declared version has a
  matching heading; an `## [Unreleased]` prose entry has no machine-checkable contract.

### Steps

Code is specified, not quoted: every constant, signature, branch and assertion is named.

1. In `tests/scripts/test_run_tests.py`, add `BinaryIO` to the `typing` import (line 13) and a
   module-level stub `InterruptingProcess(interrupts: int, capture: BinaryIO, payload: bytes)`
   after `BinaryStderr` (ends line 54). It generalises the local `InterruptedProcess` it replaces:
   `returncode = 2`, `wait` writes `payload` to `capture` on the first call and raises
   `KeyboardInterrupt` for the first `interrupts` calls before returning `returncode`, and
   `terminate()`/`kill()` record `terminated`/`killed`. One interrupt is the ordinary Ctrl-C, two
   reach `terminate()`, three reach `kill()`.

2. Rewrite `test_interruption_replays_captured_output_without_traceback` (line 228) to build
   `InterruptingProcess(1, temporary_file, output)` and drop its local class, keeping every
   existing assertion unchanged.

3. Add `test_the_reap_is_the_smaller_of_the_two_bounds()`, no fixtures, asserting
   `run_tests.TERMINATE_GRACE_SECONDS < run_tests.INTERRUPT_GRACE_SECONDS`, its docstring saying
   the window's size answers to the suite ADR 0130 measures and recording why it is deliberately
   not floored against `_READINESS_TIMEOUT_SECONDS`.

4. Add `test_further_interrupts_escalate_without_escaping_main`, decorated
   `@pytest.mark.parametrize(("interrupts", "killed"), [(2, False), (3, True)])`. Build
   `InterruptingProcess(interrupts, temporary_file, payload)` for a payload containing a non-UTF-8
   byte and monkeypatch stderr, `TemporaryFile` and `Popen` exactly as
   `test_interruption_replays_captured_output_without_traceback` does; assert
   `run_tests.main() == 130`, `process.terminated`, `process.killed is killed`,
   `stderr.buffer.getvalue() == payload`, `temporary_file.closed`.

5. Add `test_a_wedged_child_is_terminated_then_killed` with a local `WedgedProcess`
   (`returncode = -9`) whose `wait` records each `timeout`, raises `KeyboardInterrupt` on the
   first call, raises `subprocess.TimeoutExpired(["pytest"], timeout)` on every later timed call,
   and returns `returncode` for the untimed one. Monkeypatch as in step 4, then assert
   `run_tests.main() == 130`, `process.terminated`, `process.killed`, and `process.timeouts ==
   [run_tests.TEST_TIMEOUT_SECONDS, run_tests.INTERRUPT_GRACE_SECONDS,
   run_tests.TERMINATE_GRACE_SECONDS, None]` — pinning routing, both constants and both rungs.

6. Extend `test_timeout_terminates_pytest_and_returns_timeout_status` (line 258): replace the
   `TimedOutProcess.wait_count` class attribute with an `__init__` setting `self.wait_count = 0`
   and `self.timeouts: list[float | None] = []`, append `timeout` at the top of `wait`, and after
   the existing `assert process.wait_count == 2` assert `process.timeouts ==
   [run_tests.TEST_TIMEOUT_SECONDS, run_tests.TERMINATE_GRACE_SECONDS]`, with a comment recording
   why the timeout arm skips the window (ADR 0130).

7. In `test_real_interrupt_preserves_pytest_diagnostic`, replace lines 362-363 (`grace = ...` and
   `budget = 2 * grace + ...`) with a budget summing `run_tests.INTERRUPT_GRACE_SECONDS`,
   `run_tests.TERMINATE_GRACE_SECONDS` and `_INTERRUPT_COLLECTION_SLACK_SECONDS`, keeping
   ADR 0129's rule that it reads its bounds from the module under test. In both failure messages
   replace the single `{grace}s` reference with the window and the reap, labelled `window` and
   `reap`, leaving the readiness, settle-interval and stderr-tail parts as they are.

8. Correct the two comments asserting a clamp this change removes: `:86-89` ("clamped by
   `run_tests._settle_interrupted`") and `:383-386` ("clamped near `2 * grace` whatever the host
   is doing"). Both become the measured fact — the settle interval now tracks host latency as
   readiness does — which strengthens the surrounding "report, do not attribute" rule and should
   say so. Leave `:57-58`: the readiness ceiling is still a hang ceiling.

9. Confirm the expected red: `uv run --no-sync pytest tests/scripts/test_run_tests.py --no-cov`
   fails exactly the four Verification tests plus
   `test_real_interrupt_preserves_pytest_diagnostic`, which step 7 has just pointed at the absent
   `TERMINATE_GRACE_SECONDS`. No other test may fail.

10. In `scripts/run_tests.py`, replace line 11's single constant with
    `INTERRUPT_GRACE_SECONDS = 300` and `TERMINATE_GRACE_SECONDS = 3`.

11. Replace `_settle_interrupted` (lines 21-31) with two functions.
    `_stop(process: subprocess.Popen[bytes]) -> None` calls `process.terminate()`, then
    `process.wait(timeout=TERMINATE_GRACE_SECONDS)` inside `try: ... except
    (subprocess.TimeoutExpired, KeyboardInterrupt):` whose handler calls `process.kill()` and then
    `process.wait()` inside its own `try: ... except KeyboardInterrupt: pass`, so the terminal rung
    cannot escape either; its docstring says it stops a child that will not exit on its own,
    escalating to SIGKILL, and that a `KeyboardInterrupt` here is a further Ctrl-C asking to stop
    now. `_settle_interrupted(process: subprocess.Popen[bytes]) -> None` keeps its signature and
    calls `process.wait(timeout=INTERRUPT_GRACE_SECONDS)` under the same two-exception `except`,
    whose handler calls `_stop(process)`; its docstring keeps the existing summary line and adds
    that the wait returns the moment the child exits, so only a child that has not exited pays the
    window (ADR 0130) — not "only a child that ignores SIGINT", which is false for a child that
    never received one.

12. In `main`, point the timeout arm at `_stop` (line 58), leaving the `KeyboardInterrupt` arm on
    `_settle_interrupted`.

13. Confirm green: `uv run --no-sync pytest tests/scripts/test_run_tests.py --no-cov` passes,
    including `test_real_interrupt_preserves_pytest_diagnostic`.

14. Add the `CHANGELOG.md` entry under `## [Unreleased]`, in its `### Changed` subsection
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
`KeyboardInterrupt` arm and `_stop` directly from the `subprocess.TimeoutExpired` arm; every wait
in the ladder catching `KeyboardInterrupt`; both guardrail commands exit 0; `docs/adr/0129-*.md`
unmodified. Rollback is a revert of the one commit.
