# ADR 0112: Validate Explicit UUID Path Arguments at the Request Boundary

## Status

Accepted

## Context

Client methods interpolate both UUID-only identifiers and legitimate names into REST paths. The
existing request-boundary guard rejects raw and once-decoded dot segments, but a blanket UUID
check over path segments would reject name-addressed resources. The string path alone does not
retain the argument name or its public contract.

## Decision

Add a request-boundary helper that accepts the completed path plus an explicit mapping of
UUID-only argument names to values. It validates each mapped value with
`resource_identity.is_uuid` before delegating to the existing request method. UUID-only client
methods use this helper for their interpolated UUID arguments; name-addressed arguments remain on
the ordinary request path. A refusal raises `HMCError`, names only the argument, and occurs before
HTTP construction. The existing raw and decoded dot-segment guard remains independently active.

## Consequences

The validation decision stays tied to an argument contract instead of being inferred from route
text. New UUID-only path interpolation must opt into the helper, which is reviewable but not
automatically discoverable. Existing callers that deliberately use name-like fixture identifiers
continue to work only on methods whose contracts permit names; tests for UUID-only methods must use
canonical UUIDs.

## Considered & rejected

- **Reject every non-UUID resource segment at `_request`.** verified: issue #278 and merged PR
  #266 identify both UUID-only and name-addressed path fragments; the path string carries no
  argument contract, so this rejects legitimate names.
- **Validate independently in every client method.** judgment: this duplicates error construction
  and the no-HTTP ordering guarantee across the call sites instead of keeping enforcement at the
  request boundary.
- **Infer UUID requirements from collection names.** verified: `rg -n 'VolumeGroup|VirtualDisk'
  src/hmc_mcp/client` at `4f7e252d` shows the same route builders carry UUID-only parent arguments
  and name-addressed child arguments, so collection names are insufficient metadata.
- **Keep only the dot-segment guard.** judgment: it leaves the documented UUID-only contract
  unenforced and preserves the upstream normalization assumption issue #278 was opened to remove.
