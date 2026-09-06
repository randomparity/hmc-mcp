# ADR 0126: Keep operation maturity sparse and evidence scoped

## Status

Accepted

## Context

The capability ledger from ADR 0125 identifies reference rows and registered
operations, but deliberately does not claim that an implementation was reviewed,
tested, or exercised on hardware. Issue #622 requires those facts to remain separate
from both `ToolSecurity` authorization metadata and runtime capability checks.

A single maturity label cannot express an implemented operation whose contract has not
been reviewed, whose automated checks pass, and whose live checks differ by HMC release
or operation variant. It also makes a historical pass look current after a shared
dependency changes.

## Decision

Add a sparse, versioned `docs/capabilities/maturity.json` catalog keyed by the stable
operation IDs in `operations.json`. An operation absent from the catalog has unknown
maturity; absence is not an error and grants nothing.

Each record carries an independent implementation state (`absent`, `partial`, or
`implemented`) and explicit implemented and missing scope descriptions. `partial`
requires both lists, `implemented` requires only implemented scope, and `absent`
requires only missing scope.

Evidence is a list of observations. Every observation names a stable ID, one channel
(`contract-review`, `automated`, or `live`), exact operation variant and applicable
parameters, one result (`not-run`, `skipped`, `failed`, or `passed`), whether it is
`current` or `stale`, and a public-safe source or reason. Live observations additionally
name the HMC release/build, hardware family, firmware, licensing, topology, scenario,
implementation revision, and deployed revision. A live pass also requires asserted
postconditions and cleanup of `passed` or `not-required`.

Promotion is a query over observations, not a stored overall grade. Only a current
`passed` observation promotes its own channel, operation variant, parameter scope, and,
for live evidence, exact environment and scenario. Mocked success can promote only the
automated channel. A skip, opt-in, issue closure, transport success without asserted
postconditions, or evidence from a different environment cannot promote live evidence.

When an operation or shared dependency changes, affected passing observations become
`stale` with an invalidating revision and reason. Re-evaluation adds a new current
observation. A current failure is a regression for only its exact scope; historical
passes remain stale history rather than being deleted or treated as current.

The catalog records no runtime eligibility. Implemented but unverified operations retain
their existing admission under the current runtime authorization, ownership, validation,
capability, and safety guards. Maturity never bypasses or widens those guards. A known
failure may motivate a separately reviewed runtime restriction, but this catalog does
not create one.

Extend the existing capability-inventory validator and test module to validate the
catalog and its join. Do not add a runtime loader or dependency.

## Consequences

Consumers can distinguish implementation progress from each evidence channel without
inventing a global score. Sparse records let F2 establish the contract using
representative operations while later verification work adds evidence incrementally.
The explicit environment makes live claims narrower and more verbose; that is the cost
of preventing cross-hardware promotion. Current/stale replacement is authored rather
than inferred from Git history, so review and validation must reject contradictory
current observations.

Public evidence must use stable anonymous tokens for identifiers and omit secrets,
addresses, and internal locations. The validator proves shape and consistency, not the
truth of an observation or the continued correctness of runtime behavior.

## Considered & rejected

- **One maturity enum per operation.** judgment: it collapses independent implementation,
  review, automated, live, and environment dimensions into an ordering that the issue
  explicitly rejects.
- **Put maturity on `ToolSecurity`.** verified: `src/hmc_mcp/tool_registry.py` defines
  authorization metadata consumed during server composition, while issue #622 requires
  maturity to remain separate from authorization and runtime capability.
- **Require a maturity row for every registered operation immediately.** judgment: 156
  placeholder rows add maintenance without evidence; sparse absence already represents
  unknown and lets verification children add only grounded records.
- **Derive currency from the current Git revision.** judgment: a shared dependency can
  invalidate only some operation scopes, so repository-wide revision inequality would
  stale unrelated evidence and still miss external environment changes.
- **Store a promoted aggregate.** judgment: a cached grade can disagree with its scoped
  observations; deriving promotion preserves the dimensions that explain the claim.
- **Do nothing.** verified: issue #622 and parent #620 requirements 6–11 require a
  machine-checkable maturity and promotion contract before runner and discovery work.
