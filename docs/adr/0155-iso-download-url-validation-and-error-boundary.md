# ADR 0155: Validate ISO URL bytes and translate download construction failures

## Status

Accepted (2026-09-16)

## Context

Issue #841 identifies an independent ISO download client outside `_request`:
`urlparse` strips CR/LF/tab while the original string reaches httpx, whose
`InvalidURL` is not covered by ADR 0148's HMC request boundary.

## Decision

Use both input validation and local exception translation in storage operations.
Reject ASCII C0 controls and DEL before the first `urlparse` call as `HMCError`.
After existing scheme and allowlist checks admit a source, require `httpx.URL`
to build it before HMC or download work, returning the original string rather
than a normalized replacement. Translate that build's `InvalidURL` and any
`InvalidURL` escaping the standalone download call into `HMCError`, chaining
the cause. Diagnostics omit the raw URL. These are refused requests, not
`HMCTransportError` failures for which retry or fallback is appropriate.

Keep existing scheme, host, default/explicit port, redirect and size rules.
Existing policy refusals remain `ValueError`. No caller migration is needed.

## Consequences

Control-bearing URLs fail before operation-side HMC, filesystem or network
work. Buildability follows the installed httpx parser without duplicating its
host, port or length grammar. The local catch remains a backstop if a later
httpx build rejects a source. URL normalization, allowlist redesign and HMC
constructor behavior (#840) remain outside this decision.

## Considered & rejected

- **Only translate at download.** verified: `upload_iso` resolves VIOS and
  inventories media before downloading in `resources.py`; this would leave
  malformed sources admitted until after those side effects.
- **Only reject CR/LF/tab.** verified: httpx 0.28.1 `_urlparse.py` rejects
  ASCII C0 controls and DEL, as well as malformed hosts and overlong URLs.
  A guard limited to three characters would not cover the stated boundary.
- **Only prevalidate.** judgment: retaining a narrow catch at the independent
  dependency call gives the operation an explicit error boundary without
  relying solely on the earlier parser check.
- **Route downloads through `_request`.** judgment: coupling an independent
  streamed public download to the authenticated HMC request mechanism is a
  larger ownership change than this local refusal requires.
- **Do nothing.** verified: issue #841 records raw `InvalidURL` at the tool
  layer; leaving the path unchanged does not satisfy the issue.
