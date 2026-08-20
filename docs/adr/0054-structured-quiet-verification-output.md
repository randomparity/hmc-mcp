# ADR 0054: Structured quiet verification output

## Status

Accepted

## Context

The canonical `just test` command currently emits pytest progress, a per-file
missing-lines coverage table, warnings, and a final summary. A successful run
produces roughly 13.5 KiB across 192 lines on the current 2,449-test suite. The
canonical `just smoke` command also prints every exposed MCP tool. These details
are useful while diagnosing a failure but consume agent context on the ordinary
successful path.

Pytest's quiet flags do not provide the required contract. With pytest 9.1.1,
`-qq` and `-qqq` still print progress while removing the passing summary.
Pytest's terminal text is presentation output rather than a stable data
interface, so extracting a summary from it would couple the runner to wording
and layout changes.

## Decision

The canonical test recipe will call a repository-owned Python runner. The runner
will execute pytest with combined output captured, JUnit XML enabled, coverage
output disabled, and coverage data confined to its temporary directory. A
successful run will derive test counts and duration from JUnit XML and the
coverage percentage from coverage.py's JSON output, then print one summary line.
On failure it will replay the captured combined output and return pytest's exit
status.

`just test-verbose` will invoke pytest directly with the missing-lines coverage
report enabled. The smoke script will print only its handshake and tool count by
default and accept `--verbose` to list tool names; `just smoke-verbose` will use
that flag. `just verify` will continue to depend on the canonical quiet recipes.

The coverage source, exact 90% floor, precision, and configuration-location
guards remain unchanged. Only coverage presentation changes.

## Consequences

- Successful test output becomes one stable, bounded line regardless of suite
  size or the number of measured files.
- A failed run is intentionally noisy because its full combined diagnostic is
  the information needed to act.
- The runner waits for pytest to finish before printing output. This trades live
  progress for context conservation on the canonical agent path; the verbose
  recipe remains available for long-running interactive diagnosis.
- The runner owns temporary JUnit and coverage files and removes them on every
  exit, so verification leaves no untracked artifacts.
- The solution uses only Python, pytest, pytest-cov, and coverage.py already
  present in the locked development environment.

## Considered & rejected

- **Increase pytest quietness.** verified: `uv run --no-sync pytest -c
  /dev/null -qqq -p no:cacheprovider <passing-test>` with pytest 9.1.1 on
  x86_64 Linux still printed the progress line and no passing summary.
- **Scrape pytest's terminal summary and coverage table.** judgment: terminal
  presentation is a brittle interface for a repository guardrail; JUnit XML and
  coverage JSON already expose the required values structurally.
- **Pipe pytest through `tail` or `grep`.** judgment: preserving the producing
  command's status and replaying complete failures becomes shell-dependent and
  conflicts with the repository's bare-guardrail discipline.
- **Keep the current output.** verified: `just test` at the issue's base commit
  emitted 13,524 bytes over 192 lines for 2,449 passing tests on pytest 9.1.1,
  pytest-cov 7.1.0, coverage 7.15.4, x86_64 Linux.

