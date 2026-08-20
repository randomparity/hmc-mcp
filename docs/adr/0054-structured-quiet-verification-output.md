# ADR 0054: Capture successful verification output

## Status

Accepted

## Context

[ADR 0034](0034-exact-coverage-gate.md) requires exact package coverage
enforcement and also selected `term-missing` presentation. This record
supersedes only that presentation choice; ADR 0034's source, floor, precision,
denominator, configuration-source, and guard decisions remain accepted.

The canonical `just test` command currently emits pytest progress, a per-file
missing-lines coverage table, warnings, and a final summary. A successful run
produces roughly 13.5 KiB across 192 lines on the current 2,449-test suite. The
canonical `just smoke` command also prints every exposed MCP tool. These details
are useful while diagnosing a failure but consume agent context on the ordinary
successful path.

Pytest's quiet flags do not provide the required contract. With pytest 9.1.1,
`-qq` and `-qqq` still print progress while removing the passing summary.
Pytest's terminal text is presentation output rather than a stable data
interface, so the quiet path must not extract data from its wording or layout.

## Decision

The canonical test recipe will call a repository-owned Python runner. The runner
will execute pytest with combined output captured and the terminal coverage
report disabled. It removes environment variables that can override pytest or
coverage configuration. On success it will discard the captured presentation
and print one fixed summary stating that the tests and configured coverage gate
passed. On failure it will replay the combined output and preserve the child
status, including conventional shell encoding for signal termination.

`just test-verbose` will invoke pytest directly with the missing-lines coverage
report enabled. The smoke script will print only its handshake and tool count by
default and accept `--verbose` to list tool names; `just smoke-verbose` will use
that flag. `just verify` will continue to depend on the canonical quiet recipes.

The global pytest `addopts` will replace `--cov-report=term-missing` with an
empty `--cov-report=` selector; `test-verbose` will opt back into
`--cov-report=term-missing`. The `--cov` source, exact 90% floor, precision,
denominator, configuration source, and their guards remain unchanged. Only
coverage presentation changes, and this scoped decision supersedes ADR 0034
only for that presentation choice and its terminal-report consequence.

## Consequences

- Successful test output becomes one stable, bounded line regardless of suite
  size or the number of measured files. It deliberately omits counts, duration,
  and percentage rather than adding a structured post-processing stage.
- A failed run is intentionally noisy because its full combined diagnostic is
  the information needed to act.
- Capture is capped at 1 MiB. At that threshold the runner replays the captured
  prefix and switches to live passthrough, trading quiet pathological success
  output for bounded storage and complete ordered diagnostics.
- The runner waits for pytest to finish before printing output. This trades live
  progress for context conservation on the canonical agent path; the verbose
  recipe remains available for long-running interactive diagnosis.
- The runner is repository-owned code that must be maintained, but its boundary
  is limited to one subprocess, one captured stream, and exit-status handling.
- The solution uses only Python, pytest, and pytest-cov already present in the
  locked development environment.

## Considered & rejected

- **Increase pytest quietness.** verified: `uv run --no-sync pytest -c
  /dev/null -qqq -p no:cacheprovider <passing-test>` with pytest 9.1.1 on
  x86_64 Linux still printed the progress line and no passing summary.
- **Parse pytest's terminal summary and coverage table.** judgment: terminal
  presentation is a brittle interface for a repository guardrail, and exact
  counts or percentages are not required to establish that both configured
  gates passed.
- **Post-process JUnit XML and coverage JSON.** judgment: those structured
  formats could reproduce exact counts and percentage, but would add artifact
  lifecycle, parsing, and a second subprocess failure after pytest had already
  passed. A fixed success statement satisfies the confirmed compact result and
  coverage-summary requirement with less owned code.
- **Pipe pytest through `tail` or `grep`.** judgment: preserving the producing
  command's status and replaying complete failures becomes shell-dependent and
  conflicts with the repository's bare-guardrail discipline.
- **Keep the current output.** verified: `just test` at the issue's base commit
  emitted 13,524 bytes over 192 lines for 2,449 passing tests on pytest 9.1.1,
  pytest-cov 7.1.0, coverage 7.15.4, x86_64 Linux. Judgment: paying a small,
  focused subprocess wrapper once is proportionate to removing that recurring
  context cost from every successful agent run.
