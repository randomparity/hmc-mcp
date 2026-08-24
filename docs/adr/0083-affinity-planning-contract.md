# ADR 0083: Affinity planning is an explicit read-only contract

## Status

Accepted

## Context

IBM's Power 9 `lsmemopt` command reference exposes current scores and `calcscore` predictions at
partition and system scope. Predictions accept either partition names or IDs for prioritized and
excluded sets, and the reference states that a calculated score is potential rather than a
guaranteed achieved result. Repository issue #311 requests that planning contract; issue #310 and
`src/hmc_mcp/ssh_commands.py` supply current partition scores only.

## Decision

Keep current observations and planning separate in names and results. Add a validated
`MemoptLparSelector` value that contains either names or IDs, never both, and pass optional
prioritized and excluded instances to `calcscore` operations. An explicit selector is non-empty,
contains duplicate-free nonblank structurally safe names or duplicate-free positive IDs. When both
are present, prioritized and excluded selectors use the same representation and cannot overlap.
Each selector's aggregate encoded value is capped at 4096 UTF-8 bytes as a conservative package
request-safety ceiling for the remote command budget, not as a claimed HMC capability.
Expose current system score,
predicted partition scores, and predicted system score as shared async operations and mirror them
through MCP and CLI. Preserve HMC fields while requiring the scope-specific current and predicted
score fields. Every prediction result includes `prediction_guaranteed: false`.

The feature only invokes `lsmemopt`; it never invokes `optmem` or changes HMC state.

## Consequences

Callers can safely distinguish observation from planning and construct selector scenarios without
assembling command fragments. Names and IDs remain separate because the HMC grammar gives them
different flags. Unsupported firmware and HMC command failures remain actionable `HMCCLIError`
failures. The facade gains public operations and one package-owned input type under ADR 0029.
That supported-contract expansion requires a minor release. System scores can be unavailable on
systems configured with multiple resource groups; because resource-group selection is excluded,
that capability limitation remains an actionable error rather than an invented fallback.
Tests cover selector invariants, capability and command failures, malformed and incorrectly shaped
results, and current-versus-predicted fields. Live-runner coverage invokes only score queries and
contains no `optmem` path.

## Considered & rejected

- **Keep current LPAR scores only.** judgment: it cannot compare current system state with the
  read-only predicted LPAR and system scenarios requested by issue #311.
- **Add prediction flags to the existing current-score functions.** judgment: one function whose
  return contract changes with a boolean makes current and predicted results easy to confuse.
- **Accept comma-delimited strings.** judgment: raw strings expose HMC grammar to callers and make
  validation and shell safety presentation-dependent.
- **Run `optmem` after calculation.** verified: issue #311 excludes invoking `optmem`; IBM's Power
  9 `lsmemopt` command reference defines `calcscore` as calculation rather than optimization.
- **Guarantee the predicted score.** verified: IBM's Power 9 `lsmemopt` command reference defines
  it as a potential score, and IBM's affinity-score guidance says a perfect score may be unattainable.
