# ADR 0204: Opt-in verification timings

## Status

Accepted

## Context

Issue #1430 needs timing evidence while ADR 0099 keeps ordinary success quiet.
The existing runner already owns pytest environment filtering and lifecycle.

## Decision

Add a closed `--timings` runner option and the equivalent
`HMCPCTL_TEST_TIMINGS=1` switch for propagation through `just verify`.
Request pytest's 30 slowest setup/call/teardown durations with no minimum cutoff
and replay successful output only when opted in. Do not forward pytest options.
Use `just --time` for per-recipe wall times. Diagnostic recipes reuse the existing
verification graph, exact configured coverage gate and subprocess lifecycle.
This extends ADR 0099's presentation choice only for explicit diagnostics.

## Consequences

Diagnostics are noisier and timings depend on host load and cache state.
The switch changes presentation only; ordinary commands retain quiet defaults.
No profiler dependency, timing threshold or second verification graph is added.

## Considered & rejected

- **Custom gate timer.** verified: installed just 1.57.0 documents `--time`
  as printing recipe execution time. Judgment: duplicating that facility and
  the gate graph would add unnecessary ownership.
- **Forward pytest options.** judgment: arbitrary arguments create coverage and
  suite-selection bypasses beyond the requested timing surface.
- **Always retain successful output.** judgment: this loses the quiet default
  required by #1430 and ADR 0099.
