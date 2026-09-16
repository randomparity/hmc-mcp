# Translate `httpx.InvalidURL` at the `_request` waist

Issue #826. Decision: [ADR 0148](../../adr/0148-unbuildable-request-urls-raise-hmc-error.md).

## Problem

`httpx.InvalidURL` shares no base with the two httpx families `_request`
translates, so a path or query value carrying a character httpx refuses escapes
the waist as a third-party type — through entry points whose error contract is
the six names `hmc_mcp.api` exports (ADR 0118). ADR 0148 holds the evidence.
Three comments in `tests/unit/test_request_path_safety.py` state the gap in prose
and assert nothing about it.

## Scope

One new `except httpx.InvalidURL` branch in `_request` raising `HMCError`, with a
message naming the HTTP method and httpx's reason and not the path; ADR 0148; and
assertions replacing the three comment-only notes. No other file changes.

Ownership stands: `_request` already owns transport-exception translation and
`_reject_dot_segments` local path refusal at the same waist, so this is a clean
extension of the first carrying the second's exception type. Nothing migrates.
Out of scope, with owners: `httpx.DecodingError` and `httpx.TooManyRedirects` (no
owner; different family, not verified reachable); `httpx.CookieConflict` (no
owner; this client uses no cookie jar); superseding ADR 0145's or ADR 0146's
per-argument encoding (owned by the merged #819 and #818).

## Failure model

- **Actors and deployments.** A local operator through the `hmc-mcp` CLI; an MCP
  client through the stdio server; a library consumer importing `hmc_mcp.api`.
  Each reaches `_request` with argument values it supplies.
- **Invariants and assets at stake.** The `hmc_mcp.api` error contract (ADR 0118).
  The distinction `HMCTransportError` carries, since three catchers retry
  elsewhere on it. No caller-supplied control character reaching a message or log.
- **Accepted failure classes.** The message does not name which argument carried
  the character, because `_request` holds a path it did not build — accepted, as
  httpx's reason names the character and its position, the trade
  `_reject_dot_segments` already makes. `httpx.InvalidURL` raised outside
  `_request` is untouched — accepted, bounded to the waist. An over-long URL (past
  httpx's 65536-character limit) becomes an `HMCError` rather than being refused
  earlier — accepted, a refusal either way.
- **Covered elsewhere.** Keeping `group` and `property_name` off this handler at
  all: ADR 0145, ADR 0146. Path-form refusal: `_reject_dot_segments` (ADR 0143).

## Threat model

- **Boundaries.** None added or widened. This is the failure edge of one existing
  boundary — caller-supplied path and query values entering `httpx.build_request`
  — plus one new destination, the exception message, which reaches logs.
- **Actors.** The untrusted input is those argument values. The HMC is not an
  input here: `follow_redirects` is `False` on this client, and httpx converts an
  `InvalidURL` from a `Location` header into `RemoteProtocolError`, a
  `TransportError` the existing handler already catches.
- **Control.** Refuse and translate; repair no value. The message omits the path
  and quotes only httpx's reason, whose interpolations go through `!r`, so the
  rejected character renders escaped.
- **Out of scope.** Whether a value should have been encoded before reaching the
  waist (ADR 0145 and ADR 0146 own that), and each httpx family excluded above.

## Success

1. `_request` raises `HMCError` — not `httpx.InvalidURL`, not
   `HMCTransportError` — for a path or query value httpx refuses to build a URL
   from, for each of CR, LF and tab.
2. That message contains the HTTP method and httpx's reason, and no CR, LF or tab.
3. The two existing handlers are unchanged and still cover timeouts and transport
   errors.
4. The values ADR 0145 and ADR 0146 govern — the `_GROUP_VALUES` tuple and
   `test_a_quick_property_name_cannot_re_point_the_request`'s parameter list —
   still reach the wire encoded, unchanged by this branch.
5. ADR 0148 exists and `just adr-numbering` passes.

## Validation

Every criterion is machine-checkable, and the plan's Verification inventory holds
each one's mode, test, expected red, and green command. Criteria 1, 2 and 3 are
one parametrized case over CR, LF and tab in
`tests/unit/test_request_path_safety.py`; its `not isinstance(exc,
HMCTransportError)` assertion is criterion 3's, because a future httpx giving
`InvalidURL` a shared base would make the existing handler catch it first.
Criterion 4 is the two existing encoder tests staying green unchanged, and
criterion 5 is `just adr-numbering`.
