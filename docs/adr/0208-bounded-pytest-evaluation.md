# 0208: Evaluate bounded pytest before adopting parallel execution

## Status

Proposed

## Context

Issue #1434 permits either measured adoption or a measured NO-GO while preserving
complete tests, branch coverage, resource bounds and subprocess behavior.
The integrated baseline follows #1430, #1432, #1433 and #1431.

## Decision

Evaluate exactly two pytest-xdist workers with file-level distribution against
serial execution in identical environments. Stage isolation, coverage and
lifecycle fitness before repeated timings. Measure both local resource profiles
and both native CI architectures. Keep ordinary verification serial until the
candidate demonstrates stable benefit and the production design is reviewed.
Remove the temporary measurement workflow and executable artifacts before
shipping the decision; retain the evidence and its limitations.

## Consequences

The experiment adds temporary tooling and a pinned development dependency.
Native samples incur hosted work. A rejected candidate leaves no permanent
parallel execution surface or new hardware requirement. Measured failures and
sampling/cache limits remain part of the decision evidence.

## Considered & rejected

- **Home-grown pytest partitioning.** judgment: duplicates collection, scheduling
  and coverage-combination ownership already supplied by pytest-xdist/pytest-cov.
- **Unbounded automatic workers.** judgment: cannot satisfy the requested resource
  bounds and may multiply collection memory on constrained systems.
- **Adopt without measurement.** judgment: does not satisfy #1434's stability,
  memory, native-architecture and repeatable-benefit requirements.
- **Keep serial without an experiment.** judgment: provides no evidence for the
  explicitly requested evaluation.
