# Live-runner failure-output redaction implementation

Goal: prevent sensitive live-runner failure context from crossing the terminal
or JSON boundary while retaining useful diagnostics.

Architecture: a private string sanitizer sits at the existing `RunState.record`
boundary for every `FAIL` result, whether it came from a caught tool exception
or a subtask, and is reused by the pre-state configuration error renderer.
Bootstrap output becomes generic. The implementation uses Python 3.11+
standard-library regular expressions and the existing pytest runner.

## Global Constraints

Python 3.11+; no new dependencies; redact secret values, URL userinfo, bare
hostnames, and local filesystem paths; preserve error classes and ordinary
diagnostic text; do not alter successful tool payloads.

Expected implementation size: 55–100 changed lines (S) — one bounded helper,
one record-boundary change, one pre-state render change, one bootstrap message,
and focused regression tests.

## File map

- Modify `scripts/live_test_runner.py`: own sanitization and safe bootstrap
  output.
- Modify `tests/test_live_runner.py`: prove failure ingestion, rendering, and
  persisted representation omit sensitive values.

## Task 1 — sanitize failure ingestion

**Interfaces:** consumes `str`; defines private
`_redact_failure_text(value: str) -> str`; `RunState.record()` applies it to
every `FAIL` value and remains the sole terminal/persisted rendering path.

**Verification:**

- Contract: a caught or directly-recorded failure contains no password, URL
  userinfo, hostname, or local path after it reaches `record()`. Mode:
  focused-test. Add a scripted client failure and a direct
  `record(..., "FAIL", ...)` case with all four values; they fail before the
  helper exists. Green command:
  `uv run --no-sync pytest tests/test_live_runner.py -q --no-cov`; expect all
  selected tests to pass.
- Contract: ordinary diagnostics remain readable. Mode: focused-test. Assert
  the exception class and safe phrase remain in the same failure test; expect
  the command above to pass.
- Contract: a pre-state configuration failure prints no path. Mode:
  focused-test. Configure a missing context file, capture `main()` output, and
  assert the path is absent while the configuration failure remains actionable.
  Green command: `uv run --no-sync pytest tests/test_live_runner.py -q --no-cov`;
  expect all selected tests to pass.

Steps:

1. In `scripts/live_test_runner.py`, add `_redact_failure_text(value: str) ->
   str` using compiled standard-library patterns for named secret assignments,
   URL userinfo, hostnames, and absolute paths. Replace each match with an
   explicit `<REDACTED-…>` token.
2. Change `RunState.record()` so it applies `_redact_failure_text()` to `FAIL`
   data before constructing the stored entry or printing the error.
3. Change the direct configuration-error rendering in `main()` to apply the
   same helper before printing the caught exception.
4. Replace the bootstrap success message with a generic configured-credentials
   acknowledgement that has no path or host interpolation.
5. Run the focused command above; expect green. Run `just lint` and `just
   typecheck`; expect zero findings.

Acceptance: every caught and directly recorded failure crosses one sanitizer
before state, stdout, or JSON; pre-state failure rendering and bootstrap identify
no local path or host.

## Task 2 — prove output representations

**Interfaces:** consumes `RunState.call()` and `RunState.record()`; asserts the
recorded entry, stdout, and JSON-ready entry share the safe representation.

**Verification:**

- Contract: record/render does not reintroduce sensitive data. Mode:
  focused-test. Capture stdout after recording the sanitized failure and inspect
  `state.results`; before the implementation the secret is present. Green
  command: `uv run --no-sync pytest tests/test_live_runner.py -q --no-cov`;
  expect all tests to pass.

Steps:

1. Add direct tests in `tests/test_live_runner.py` for a synthetic failure that
   includes a password assignment, credentialed URL, hostname, and absolute
   path. Assert their literals are absent and redaction tokens, error class,
   and safe text are present.
2. Record that data and assert captured stdout and `state.results[0]["data"]`
   omit every literal.
3. Add the missing-configuration test and assert its captured output omits the
   configured path while retaining the error category.
4. Run the focused command above, then `just test`, `just smoke`, and `just
   verify`; expect each command to exit zero. Review the diff and commit the
   implementation with `fix: redact live runner failures`.

Acceptance: regression tests cover both failure ingestion and the common
terminal/persisted representation without modifying PASS or SKIP behavior.
