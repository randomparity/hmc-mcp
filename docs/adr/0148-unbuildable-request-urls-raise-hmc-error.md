# ADR 0148: An unbuildable request URL is refused as an HMCError

## Status

Accepted (2026-09-15)

## Context

`HMCClient._request` is the waist every REST call passes through, and it
translates exactly two httpx exception families: `httpx.TimeoutException` and
`httpx.TransportError`, both to `HMCTransportError`. `httpx.InvalidURL` descends
from neither — its MRO is `(InvalidURL, Exception,
BaseException, object)` on the locked httpx 0.28.1 — so it escapes `_request`
untranslated and a caller holding the six-name `hmc_mcp.api` contract (ADR 0118)
sees a third-party type. Verified on that httpx, `build_request("GET",
"/rest/api/uom/LogicalPartition?group=None\rX-Evil: 1")` raises `InvalidURL:
Invalid non-printable ASCII character in URL, '\r' at position 41.` ADR 0143, ADR
0145 and ADR 0146 each hardened one interpolated argument against this class and
each recorded the waist translation as a follow-up candidate outside its own
surface. Issue #826 claims it.

## Decision

**`_request` translates `httpx.InvalidURL` into `HMCError`. The message names the
HTTP method and httpx's own reason, and never the request path.**

`HMCError`, not `HMCTransportError`, on two grounds. It is what the sibling
refusal at this same waist raises: `_reject_dot_segments` runs on the line above
the `try` and raises `HMCError` because — in its own words — it is "a request
this client will not send". And `HMCTransportError` means the route did not
answer and another might: its three catchers all act on that reading, one
retrying the session on port 12443 and two falling back from REST to the SSH CLI
in `src/hmc_mcp/ssh/selectors.py`. A URL httpx will not build does not become
buildable on another route. Neither catcher is reachable by this class today —
the logon path is a literal, both resolvers UUID-guarded — so this is contract
fit, not a live defect.

Never the path, because the value reaching this handler is the one httpx rejected
as non-printable, and interpolating it would carry a caller's CR or LF into a
message and from there into logs. httpx's reason is safe to quote: its
non-printable branch renders the offending character through `!r`, and its other
branches interpolate only httpx-internal component names.

## Consequences

- A malformed-URL failure now satisfies the same `except HMCError` a caller
  writes for this waist's other refusal, `_reject_dot_segments`; per-argument
  validators still raise `ValueError`. Nothing about the call changes: it escaped
  `_request` before and escapes it now, typed and worded anew.
- This is a backstop, not a replacement: ADR 0145's `group` encoder and ADR
  0146's `property_name` encoder still run first, so a value they encode never
  reaches this handler at all.
- The two `except HMCError` sites in `core.py` that degrade a discovery read to
  `names = None` build their path from a `_reject_unknown_uom_type`-restricted
  type, so neither is reachable by this class.
- Residual: the message does not name the offending argument, because `_request`
  holds a path it did not build — the trade `_reject_dot_segments` already makes.

## Considered & rejected

- **Keep closing one argument at a time.** verified: at `c856827e`,
  `get_uom_path` hands a caller-supplied whole path to `_get` unchanged, so
  neither ADR 0145's nor ADR 0146's encoder covers it. judgment: an unbounded
  series of per-argument guards compensating for one shared function.
- **Translate to `HMCTransportError`, whether as its own branch or by widening
  the existing handler to `except (httpx.TransportError, httpx.InvalidURL)`.**
  verified: the widened form additionally inherits that handler's interpolation
  of the raw `path`. judgment: the retry reading above, and a message that would
  describe neither case.
- **Add a new public exception type for local request refusals.** verified:
  `hmc_mcp.api.__all__` is the six names ADR 0118 pins, asserted as an exact set
  by `tests/unit/test_public_api.py`. judgment: a seventh stable name for a
  refusal the guard on the line above already spells `HMCError`.
- **Raise `ValueError`, as `_reject_unknown_uom_type` and the UUID check do.**
  verified: both are per-argument validators called where the segment is built,
  before `_request`; the guard running *at* this waist raises `HMCError`.
  judgment: `_request` cannot attribute a path it did not build to an argument.
- **Catch `Exception` at the waist.** verified: that `try` also calls
  `_read_bounded_response`, which raises `HMCError` for an over-size body and
  would be re-wrapped. judgment: it would hide programming errors as HMC errors.
