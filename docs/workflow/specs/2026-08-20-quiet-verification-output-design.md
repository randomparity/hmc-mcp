# Quiet verification output design

## Goal and authority

Issue #332 requires the canonical verification commands to conserve agent
context on success without weakening their gates or hiding actionable failures.
This design is governed by [ADR 0054](../../adr/0054-structured-quiet-verification-output.md).

The change is limited to developer verification presentation: `justfile`,
pytest/coverage presentation configuration, the smoke script, one focused test
runner, tests, and directly affected command documentation. It does not change
test semantics, the coverage denominator or 90% threshold, MCP tool inventory,
application runtime behavior, dependencies, or CI architecture.

## Measured baseline

At the base commit, a successful `just test` runs 2,449 tests and emits 13,524
bytes over 192 lines. The per-file `term-missing` coverage table dominates the
output. Passing `--cov-report=` on the command line does not cancel the
`--cov-report=term-missing` option from project `addopts`, because pytest-cov
accepts multiple reports. Therefore the project default must become an empty
coverage report and the verbose recipe must opt back into `term-missing`.

Pytest 9.1.1 still prints progress with `-qqq`, so flags alone cannot retain a
summary while suppressing progress. The quiet path must capture output.

## Command contract

`just test` invokes `uv run --no-sync python scripts/run_tests.py`. On success it
prints exactly one semantic line in this form:

```text
test: <passed> passed[, <skipped> skipped] in <seconds>s; coverage <percent>%
```

Counts and elapsed time come from pytest's JUnit XML. `passed` is total tests
minus failures, errors, and skipped tests. Zero-valued optional categories are
omitted. Coverage comes from coverage.py JSON and uses the configured display
precision. The exact elapsed representation may follow JUnit's numeric value;
tests assert its semantic fields rather than a wall-clock fixture value.

Warnings are omitted on success. A warning that must fail the suite remains a
pytest failure under existing configuration; ordinary warning detail is
available through the verbose path.

On any pytest non-zero exit, the runner writes pytest's complete combined
stdout/stderr to stderr and exits with the same positive exit code. It does not
try to parse JUnit or coverage data. If pytest is terminated by a signal, the
runner re-emits the captured diagnostics and exits non-zero; exact shell signal
encoding is outside this command's portable contract.

If pytest succeeds but the structured summary cannot be read or generated, the
runner reports the failed summary operation and exits non-zero. A passing suite
without its promised bounded result is not a successful canonical command.

`just test-verbose` invokes pytest directly with `-q` and
`--cov-report=term-missing`. It retains live pytest output, the full coverage
table, warnings, and failure diagnostics.

`just verify` keeps the existing dependency graph and uses `test`, not
`test-verbose`.

## Test runner

`scripts/run_tests.py` is a focused command adapter, not a general subprocess
abstraction. Its public-to-tests boundary is:

```python
def main(args: list[str] | None = None) -> int: ...
```

Additional arguments are forwarded to pytest after the runner-owned
`--junitxml` option. The canonical recipe supplies none. The runner rejects no
pytest arguments, but its own JUnit and coverage destinations remain last so a
caller cannot redirect the structured artifacts used for the summary.

Within a `TemporaryDirectory`, the runner:

1. sets `COVERAGE_FILE` for the child to a temporary path;
2. runs `[sys.executable, "-m", "pytest", *args,
   "--junitxml=<temporary path>"]` with stderr redirected to stdout;
3. replays the combined stream and returns immediately on non-zero;
4. runs `[sys.executable, "-m", "coverage", "json", "-o",
   <temporary path>]` against the same `COVERAGE_FILE`;
5. parses the JUnit `<testsuite>` aggregate and coverage JSON `totals`; and
6. emits the compact line.

Both subprocesses inherit the repository working directory so pytest and
coverage continue reading `pyproject.toml`. The runner changes only
`COVERAGE_FILE`, not `COVERAGE_RCFILE`, `PYTEST_ADDOPTS`, the measured source,
or the coverage floor.

## Coverage configuration

`[tool.pytest.ini_options].addopts` remains the sole package-wide measurement
site but changes its report selector to an empty report:

```toml
addopts = "--cov=hmc_mcp --cov-report="
```

`[tool.coverage.report] fail_under = 90` and `precision = 2` remain byte-for-byte
unchanged. The existing invocation-site guard is updated to pin the quiet
runner and verbose pytest recipes while retaining every disarm-vector check.
Focused pytest runs may continue to use `--no-cov` as documented.

## Smoke command

`scripts/smoke_mcp.py` adds an `argparse` `--verbose` flag. The protocol
handshake and live registry lookup remain identical. Default success output is:

```text
Connected. <N> tools exposed.
```

With `--verbose`, the first line ends in a colon and is followed by the current
sorted-as-returned tool names exactly as today. `just smoke-verbose` selects
that path; `just verify` continues to select `smoke`.

## Error handling and cleanup

- Pytest failures are replayed without truncation and retain their exit code.
- Coverage-summary subprocess failures include that subprocess's combined
  diagnostic and return its non-zero exit code.
- Missing or malformed JUnit or coverage JSON produces an actionable runner
  error naming the artifact and failed operation.
- Temporary output is owned by `TemporaryDirectory` and removed on success,
  failure, parse error, and interruption handled by Python teardown.
- The runner never pipes a guardrail through a filtering command.

## Tests

Focused unit tests replace subprocess execution with controlled completed
processes and temporary structured artifacts. They prove:

- a successful suite prints only the compact semantic summary;
- skipped tests are represented and failures/errors are not counted as passed;
- pytest failure output is replayed completely and its exit code is preserved;
- coverage JSON generation failure is visible and non-zero;
- malformed JUnit and coverage data fail with actionable messages;
- runner-owned artifact arguments and `COVERAGE_FILE` reach child processes;
- default smoke output contains only count, while verbose output lists live
  registry names;
- canonical and verbose recipes retain the coverage gate and verification
  dependency graph; and
- a real generated under-floor pytest project still exits with the exact
  configured gate diagnostic when the empty report default is active.

For each error test, the controlled child constructs the triggering condition;
tests do not assert implementation-only call counts except where one failed
boundary must stop the next operation.

## Global constraints

- Supported Python remains `>=3.11`; implementation uses only standard-library
  APIs available there.
- No dependency or lockfile change.
- Functions remain under 100 lines and complexity 8, with 100-character lines.
- Canonical guardrail remains `just verify`.
- Host architecture is x86_64; no target architecture is declared, and the
  behavior is architecture-independent.

