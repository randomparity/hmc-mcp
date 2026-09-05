# Quiet verification output design

## Goal and authority

Issue #332 requires the canonical verification commands to conserve agent
context on success without weakening their gates or hiding actionable failures.
This design is governed by [ADR 0099](../../adr/0099-structured-quiet-verification-output.md).

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
prints exactly one semantic line:

```text
test: passed; configured coverage gate passed
```

The line makes only the guarantees pytest's exit status establishes: tests
passed and pytest-cov accepted the configured coverage floor. Counts, duration,
warnings, and exact coverage percentage are intentionally omitted rather than
parsed from terminal presentation or reconstructed through a second reporting
stage. A warning that must fail the suite remains a pytest failure under existing
configuration; ordinary warning detail is available through the verbose path.

On any pytest non-zero exit, the runner copies pytest's complete combined bytes
from a disk-backed temporary file to `sys.stderr.buffer` without decoding.
Negative child return codes map to the conventional `128 + signal` shell status.
On an interactive interruption, the runner gives pytest three seconds to finish
its diagnostic before terminating and then killing it if necessary, then exits
130. Pytest writes directly to the file, so diagnostic production cannot block
behind an undrained pipe during settlement.

`just test-verbose` invokes pytest directly with `-q` and
`--cov-report=term-missing`. It retains live pytest output, the full coverage
table, warnings, and failure diagnostics.

`just verify` keeps the existing dependency graph and uses `test`, not
`test-verbose`.

## Test runner

`scripts/run_tests.py` is a focused command adapter, not a general subprocess
abstraction. Its public-to-tests boundary is:

```python
def main() -> int: ...
```

The runner exposes no pytest argument-forwarding surface: focused and diagnostic
runs use pytest directly, as the existing `--no-cov` guidance already requires.
The runner invokes `[sys.executable, "-m", "pytest"]` with combined output
written directly to a disk-backed `TemporaryFile`. This preserves byte order,
keeps arbitrary successful output out of agent context, and avoids an OS pipe
that could block interrupt diagnostics. The subprocess inherits the repository
working directory so pytest and coverage read `pyproject.toml`. It removes
`PYTEST_ADDOPTS`, `COVERAGE_RCFILE`, and `COVERAGE_FILE` from the child
environment so local overrides cannot narrow the canonical suite, disable
coverage, or redirect its data file.

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
- The temporary output file is closed on success, failure, runner exception, and
  handled interruption; replay holds one fixed-size chunk in memory.
- The runner never pipes a guardrail through a filtering command.

## Tests

Focused unit tests replace subprocess execution with controlled completed
processes. They prove:

- a successful suite prints only the fixed compact semantic summary;
- pytest failure output is replayed completely and its exit code is preserved;
- undecodable failure bytes are replayed unchanged without a runner traceback;
- successful output larger than 1 MiB remains hidden, failure output replays in
  bounded chunks, and the temporary file closes on success and failure;
- the child command is exactly `sys.executable -m pytest`, combines stderr into
  stdout, and removes only pytest/coverage override variables;
- a real process-group interrupt preserves pytest's diagnostic and exits 130,
  while signal return codes use conventional shell encoding;
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
