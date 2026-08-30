# ADR 0111: Match agent attribution environment names case-insensitively

## Status

Accepted

## Context

ADR 0040 requires authorization audit attribution to read `HMC_AGENT_ID` directly from the
environment. That preserves values which `HMCConfig.agent_id` validation would reject, but its
mechanism sentence specified an exact-case `os.environ.get("HMC_AGENT_ID")` lookup. PR #555 changed
the lookup to match pydantic-settings' case-insensitive environment-name handling, leaving that
sentence stale.

## Decision

Match environment names to `HMC_AGENT_ID` without regard to case. If several matching spellings
exist, use the last entry in `os.environ` iteration order.

Continue to read the selected value directly from the environment at audit emission. Do not route
it through `HMCConfig` or apply its validation. `attribution.source` remains
`"environment:HMC_AGENT_ID"`, and `attribution.verified` remains `false`.

## Consequences

The decision record now matches the behavior introduced by PR #555: case variants can supply the
claim, and environment order decides between multiple variants. Values that are empty, reserved,
longer than the configuration limit, or otherwise invalid for `HMCConfig.agent_id` can still be
recorded, subject only to ADR 0040's audit-field truncation. This amendment changes no runtime
behavior.

## Considered & rejected

- **Keep the exact-case sentence in ADR 0040.** That would continue to describe behavior replaced
  by PR #555.
- **Read attribution through `HMCConfig`.** That would reject or normalize evidence ADR 0040
  deliberately records directly and would change runtime semantics beyond this documentation fix.
- **Reject multiple case variants as ambiguous.** The shipped lookup already has deterministic
  last-entry precedence, and changing it is outside this amendment's scope.
