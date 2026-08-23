# RemoteRestart parameter correction

Issue: #401. Decision: [ADR 0078](../../adr/0078-explicit-remote-restart-contract.md).

RemoteRestart receives a required operation (`validate`, `recover`, `restart`, `cleanup`, or
`cancel`) and an explicit source system selector. A target selector is required except for
`cleanup`. UUID targets remain UUID parameters; name targets remain name parameters. The job also
carries the resolved LPAR UUID. `usecurrdata` is accepted only for restart and `retaindev` only for
cleanup. These rules are enforced before network submission.

The builder owns XML vocabulary and validation. The operation layer resolves selectors and keeps
the stable `LpmResult`/`JobOutcome` contract. MCP and CLI expose the same required choice and
conditional options. Failed jobs prefer `ErrorData`, then `detailedStatus`, then `result` for their
normalized error text while retaining the raw response.

Tests cover all operations, name/UUID target encoding, cleanup without a target, missing target,
invalid conditional options, cross-layer forwarding, CLI parsing, and detailed status extraction.
Live destructive execution is excluded from automated verification.

## Threat model

The existing authenticated MCP and CLI boundaries can submit a destructive HMC job. This change
adds no caller or authorization path; it narrows that boundary by requiring an explicit operation
and rejecting inconsistent inputs before submission. HMC authorization remains authoritative.
Protection against a legitimately authorized operator choosing the wrong explicit action is out of
scope; confirmation behavior remains owned by the existing tool and CLI safety framework.
