# 0026 — Client-side collection payload limits

## Status

Accepted (2026-08-15)

## Context

Public collection tools currently return complete parsed HMC UOM Atom feeds to
the agent. The result parser expands every returned entry, so large inventories
consume agent context even when the caller needs only a few resources.
`hmc_list_recent_jobs` already exposes a client-side `limit`, but sibling feed
tools have no equivalent payload control.

Measurements on HMC V10R3 SP1060 tested `limit`, `_limit`, `maxCount`, and
`count` against a 71-entry root UOM feed. The HMC rejected every candidate with
HTTP 400, `REST0123 Incorrect query parameter`. Root Job and search feeds were
also unavailable on that measured system. The probe, raw results, and method
are inspectable at commit `77c15437ec995b87806b70d95b4c90abe01d0a9d` and in
issue #154 comments. No supported source-side feed limit was found.

The largest single-resource responses need a different remedy. Adding a
`detail` mode would make one tool return incompatible shapes, contrary to
ADR 0012. The project is pre-release, but accepting polymorphic result schemas
would still create a permanent contract ambiguity.

## Decision

Every public collection tool backed directly by an HMC UOM Atom feed accepts
an optional non-negative `limit`. The tool retrieves and parses the complete
feed, then returns at most that many entries. Zero still performs the complete
request and parse before returning an empty list, so profile, authentication,
connectivity, selector, and HMC errors retain their normal behavior. Omission
preserves the existing complete result. `hmc_list_recent_jobs` retains its
existing default of 20 and the same post-parse slicing behavior.

Limits compose with existing state and parent selectors and never change the
list result shape. Negative values fail at the public boundary before an HMC
session is created. Every public `limit` parameter describes its entry units,
omission behavior, and client-side-only behavior in the generated schema.

Reduced single-resource payloads will use separately named summary tools if
they are later required. This decision neither adds those tools nor supersedes
ADR 0012.

## Consequences

Agents can bound the number of collection entries placed in the MCP response
while continuing to receive the same list-of-resource-dictionaries shape.
Existing calls remain valid because limits are optional, and the recent-jobs
default is unchanged. A limit does not bound HMC work, bytes transferred,
local parsing cost, or the size of any individual entry.

Behavior tests must prove that truncation happens only after the underlying
operation completes, including for zero, and that negative values prevent the
operation. Public schema tests must prevent empty descriptions from entering
the registry.

## Considered & rejected

**Use a source-side query parameter.** Measurements on V10R3 SP1060 show that
the HMC actively rejects the four plausible parameter names. Claiming a true
server-side bound without a supported mechanism would be false and could break
otherwise valid collection calls.

**Add `detail="summary"|"full"`.** The modes would produce incompatible
schemas from one tool and violate ADR 0012.

**Add summary tools now.** Separate names are the accepted future contract,
but no measured requirement currently selects which resources or fields those
tools should expose.

**Make every collection bounded by default.** That would silently make
previously complete inventory calls partial. Optional limits preserve existing
semantics while allowing callers to opt into a bound.

**Do nothing pending a source-side mechanism.** The measured HMC does not offer
one, but agent-facing payload size remains a current problem that safe
post-parse slicing can reduce. The public contract states the residual network,
HMC, and parsing costs rather than presenting the partial improvement as a
source-side solution.
