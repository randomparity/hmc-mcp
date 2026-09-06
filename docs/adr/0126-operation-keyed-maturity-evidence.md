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
`implemented`) and explicit implemented and missing scope objects. The same canonical
scope object is used by evidence: a variant ID plus sorted parameter bindings or
predicates. `partial` requires both lists, `implemented` requires only implemented
scope, and `absent` requires only missing scope. Evidence cannot claim a scope absent
from the implemented list, and the implemented and missing sets are disjoint.

Evidence is a list of observations. Every observation names a stable ID, one channel
(`contract-review`, `automated`, or `live`), an implemented scope object, a stable
scenario ID, one result (`not-run`, `skipped`, `failed`, or `passed`), whether it is
`current` or `stale`, the implementation fingerprint it covers, and public-safe
provenance or a reason. A parsed scope is identified by its variant and ordered
name/constraint pairs; a live environment is identified by its fixed named fields.
Live observations additionally name the HMC release/build, hardware
family, firmware, licensing, topology, implementation revision, and deployed revision.
A live pass also requires asserted postconditions and cleanup of `passed` or
`not-required`.

Promotion is a query over observations, not a stored overall grade. Only a current
`passed` observation whose exact scope is implemented, whose scenario, implementation
and deployed revisions match the query, whose implementation fingerprint is still
current, and whose provenance was accepted by a trusted channel validator can promote.
Live promotion is additionally confined to its exact environment. Format 1 defines only
`unverified` provenance and therefore admits no promoting observation; issue #623 owns
adding the first trusted live-artifact validator. Mocked success, a skip, opt-in, issue
closure, transport success without asserted postconditions, or evidence from a different
environment remains non-promoting history.

The implementation fingerprint covers all tracked runtime source, scripts, and dependency
manifests except the maturity catalog itself. This conservative repository-wide boundary
makes a code, runner, or dependency change mechanically invalidate every promoting
observation rather than silently missing a shared dependency. Re-evaluation marks the
old observation `stale` with the new implementation fingerprint and a reason, then adds a
new current observation. The fingerprint is available before commit, unlike that commit's
future Git SHA, so the source and staleness edit can pass the guard in one commit. A current
failure is a regression for only its exact scope, scenario, and environment; historical
passes remain stale history.

A live `not-run` gap names the intended scenario, prerequisites, and a durable catalog
obligation whose identity joins back to that exact operation and observation. An optional
repository issue number is a pointer, not the obligation's owner; the offline validator
does not claim that it remains open. The obligation never counts as evidence or promotion.

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
representative operations with empty evidence lists while later verification work adds
grounded evidence incrementally. Missing evidence remains unknown and is never inferred
to be `not-run`.
The explicit environment makes live claims narrower and more verbose; that is the cost
of preventing cross-hardware promotion. Repository-wide invalidation is intentionally
conservative: unrelated runtime changes may require re-evaluation, but no authored
dependency omission can leave old promoting evidence current.

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
- **Use the current commit as the fingerprint.** judgment: a commit includes the catalog
  update itself, making a same-change evidence record self-referential. A normalized hash
  of the implementation surface remains conservative without that cycle.
- **Author a per-operation dependency list.** judgment: omission would silently preserve
  exactly the stale shared-dependency evidence this contract must invalidate.
- **Store a promoted aggregate.** judgment: a cached grade can disagree with its scoped
  observations; deriving promotion preserves the dimensions that explain the claim.
- **Do nothing.** verified: issue #622 and parent #620 requirements 6–11 require a
  machine-checkable maturity and promotion contract before runner and discovery work.
