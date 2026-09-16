# Translate `httpx.InvalidURL` at the `_request` waist

Issue #826. Decision: [ADR 0148](../../adr/0148-unbuildable-request-urls-raise-hmc-error.md).

## Problem

`httpx.InvalidURL` shares no base with the two httpx families `_request`
translates, so a path or query value carrying a character httpx refuses escapes
the waist as a third-party type, past the six-name `hmc_mcp.api` error contract
(ADR 0118). ADR 0148 holds the evidence; three comments in
`tests/unit/test_request_path_safety.py` state the gap in prose.

## Scope

One new `except httpx.InvalidURL` branch in `_request` raising `HMCError`, with a
message naming the HTTP method and httpx's reason and not the path; ADR 0148; and
assertions replacing the three comment-only notes. No other file changes.
Ownership stands: `_request` already owns transport-exception translation and
`_reject_dot_segments` local path refusal at the same waist, so this is a clean
extension of the first carrying the second's exception type. Nothing migrates.
Out of scope, with owners: `httpx.DecodingError` and `httpx.TooManyRedirects` (no
owner; a different family, not verified reachable); `httpx.CookieConflict` (no
owner; no cookie jar here); superseding ADR 0145's or ADR 0146's per-argument
encoding (owned by the merged #819 and #818).

## Failure model

- **Actors and deployments.** A local operator through the `hmc-mcp` CLI, an MCP
  client through the stdio server, and a library consumer importing `hmc_mcp.api`,
  each supplying argument values; plus the HMC, whose Atom hrefs `fetch_json`
  passes to `_request` as paths.
- **Invariants and assets at stake.** The `hmc_mcp.api` error contract (ADR 0118);
  the distinction `HMCTransportError` carries, since three catchers retry
  elsewhere on it; no control character from any actor reaching a message or log.
- **Accepted failure classes.** The message does not name which argument carried
  the character, because `_request` holds a path it did not build — accepted, as
  httpx's reason names the character and its position. `httpx.InvalidURL` raised
  outside `_request`, including from `HMCClient.__init__` on a control character
  in `HMC_HOST`, is untouched — accepted as bounded to the waist, and reported as
  an unowned follow-up candidate. An over-long URL (past httpx's 65536-character
  limit) becomes an `HMCError` — accepted, a refusal either way.
- **Covered elsewhere.** ADR 0145 and ADR 0146 keep `group`/`property_name` off
  this handler; ADR 0039's `_reject_dot_segments` covers dot segments and only
  those, so no other path-form class is credited to it.

## Threat model

- **Boundaries.** None added or widened: the failure edge of one existing
  boundary — caller-supplied path and query values entering `httpx.build_request`
  — plus one new destination, the exception message, which reaches logs.
- **Actors.** Every actor above, the HMC included; the control is actor-agnostic,
  so neither refusal nor message differs by origin. A `Location` header is no
  further route: this client leaves httpx 0.28.1's `follow_redirects` default
  `False`, and httpx maps that `InvalidURL` to the already-handled
  `RemoteProtocolError`.
- **Control.** Refuse and translate; repair no value. The message omits the path
  and quotes only httpx's reason, which escapes the rejected character.
- **Out of scope.** Encoding before the waist (ADR 0145, 0146) and each excluded httpx family.

## Success

1. `_request` raises `HMCError` — not `httpx.InvalidURL`, not
   `HMCTransportError` — for a path or query value httpx refuses, for CR, LF, tab.
2. That message contains the HTTP method and httpx's reason, and neither the path
   nor a CR, LF or tab.
3. The two existing handlers are unchanged and still cover timeouts and transport.
4. The values ADR 0145 and ADR 0146 govern — `_GROUP_VALUES` and
   `test_a_quick_property_name_cannot_re_point_the_request`'s parameters — still
   reach the wire encoded, unchanged by this branch.
5. ADR 0148 exists and `just adr-numbering` passes.
6. No comment in the test module still says the exception escapes `_request`.

## Validation

Every criterion is machine-checkable; the plan's Verification inventory holds each
one's mode, test, expected red, and green command. Criteria 1 and 2 are one new
`tests/unit/test_request_path_safety.py` case parametrized over CR, LF and tab and
over both `_request` and the public `get_uom_path`; criterion 3 the existing
transport cases in `tests/unit/test_client.py` and
`tests/unit/test_response_limits.py`; criterion 4 the two existing encoder tests;
criteria 5 and 6 `just adr-numbering` and an `rg` scan of the test module.
