# ADR 0154: Constructor URL failures raise HMCError

## Status

Accepted (2026-09-16)

## Context

Issue #840 reports that `HMCClient(config)` leaks `httpx.InvalidURL` when
httpx cannot build the configured base URL. ADR 0148 translates that exception
at `_request`, but construction calls `_new_http_client` before requests exist.
The stable `hmc_mcp.api.HMCClient` entry point therefore needs its own boundary.

## Decision

Catch only `httpx.InvalidURL` around the constructor's `_new_http_client` call
and raise `HMCError` with the original exception as its explicit cause. This is
a local refusal, not a retryable `HMCTransportError`. Use a fixed diagnostic
that identifies the base-URL construction failure without interpolating the
configured host, URL, or dependency exception text. Chained tracebacks retain
the dependency's diagnostic; the fixed-message guarantee applies to `str(error)`.

Keep `_new_http_client` and its legacy-port caller unchanged. This extends
ADR 0148's error classification to public client construction, not to arbitrary
exceptions from the six-name facade. Credential validation and header errors
retain their existing owners. No public export or caller migration is required.

## Consequences

A caller catching `HMCError` catches an unbuildable base URL at construction.
No HTTP client is returned and no logon retry occurs. Successful construction,
TLS audit ordering and credential checks remain unchanged. The outer message
omits parser detail in exchange for not copying rejected configuration into
application diagnostics. ISO downloads (#841), header validation (#839), and
other exception families remain outside this decision.

## Considered & rejected

- **Do nothing.** verified: issue #840 reports raw `InvalidURL`; at base
  `b5c3ec49`, `core.py` calls `_new_http_client` without a constructor catch.
- **Validate host syntax in HMCConfig.** judgment: duplicating httpx's URL
  grammar is unnecessary to establish the requested exception boundary.
- **Translate to HMCTransportError.** verified: `core.py:logon` retries this
  subclass on the legacy port; ADR 0148 rejects that classification for an
  unbuildable URL.
- **Catch broadly or amend every facade surface.** judgment: other exception
  families and independent boundaries exceed this constructor repair.
- **Catch inside the shared factory.** verified: `_new_http_client` also serves
  `logon`'s port fallback. Judgment: this issue needs only the constructor call.
