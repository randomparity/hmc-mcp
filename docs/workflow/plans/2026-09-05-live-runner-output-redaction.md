# Live-runner failure-output redaction implementation

Goal: prevent sensitive live-runner failure context from crossing the terminal
or JSON boundary while retaining useful diagnostics.

Architecture: a private string sanitizer sits between caught tool failures and
the existing `RunState` record/render path. Bootstrap output becomes generic.
The implementation uses Python 3.11+ standard-library regular expressions and
the existing pytest runner.

## Global Constraints

Python 3.11+; no new dependencies; redact secret values, URL userinfo, bare
hostnames, and local filesystem paths; preserve error classes and ordinary
diagnostic text; do not alter successful tool payloads.

Expected implementation size: 45–85 changed lines (S) — one bounded helper,
one call-site change, one bootstrap message, and focused regression tests.

## File map

- Modify `scripts/live_test_runner.py`: own sanitization and safe bootstrap
  output.
- Modify `tests/test_live_runner.py`: prove failure ingestion, rendering, and
  persisted representation omit sensitive values.

## Task 1 — sanitize failure ingestion

**Interfaces:** consumes `str`; defines private
`_redact_failure_text(value: str) -> str`; `RunState.call()` returns its result
for a caught exception; `RunState.record()` remains the sole rendering path.

**Verification:**

- Contract: a caught failure contains no password, URL userinfo, hostname, or
  local path. Mode: focused-test. Add a scripted client failure with all four
  values; it fails before the helper exists. Green command:
  `uv run --no-sync pytest tests/test_live_runner.py -q --no-cov`; expect all
  selected tests to pass.
- Contract: ordinary diagnostics remain readable. Mode: focused-test. Assert
  the exception class and safe phrase remain in the same failure test; expect
  the command above to pass.

Steps:

1. In `scripts/live_test_runner.py`, add `_redact_failure_text(value: str) ->
   str` using compiled standard-library patterns for named secret assignments,
   URL userinfo, hostnames, and absolute paths. Replace each match with an
   explicit `<REDACTED-…>` token.
2. Change `RunState.call()` so its caught exception/traceback string is passed
   through `_redact_failure_text()` before returning `("FAIL", value)`.
3. Replace the bootstrap success message with a generic configured-credentials
   acknowledgement that has no path or host interpolation.
4. Run the focused command above; expect green. Run `just lint` and `just
   typecheck`; expect zero findings.

Acceptance: every caught failure crosses one sanitizer before state, stdout,
or JSON; bootstrap identifies no local path or host.

## Task 2 — prove output representations

**Interfaces:** consumes `RunState.call()` and `RunState.record()`; asserts the
returned failure text, recorded entry, stdout, and JSON-ready entry share the
safe representation.

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
3. Run the focused command above, then `just test`, `just smoke`, and `just
   verify`; expect each command to exit zero. Review the diff and commit the
   implementation with `fix: redact live runner failures`.

Acceptance: regression tests cover both failure ingestion and the common
terminal/persisted representation without modifying PASS or SKIP behavior.
