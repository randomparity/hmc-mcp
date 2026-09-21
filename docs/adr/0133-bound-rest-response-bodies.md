# ADR 0133: Bound REST response bodies

## Status

Accepted — 2026-09-10. Implements #770, part of #768. Configuration belongs to #771.

## Context

The shared `HMCClient._request` calls `httpx.AsyncClient.request`, which reads
the body before returning. Domain helpers therefore cannot reject large bodies
before allocation. HMCError also retains and parses complete diagnostic bodies.

## Decision

Stream in `_request`, checking declared length before iteration and counting raw
bytes before retaining each chunk. Use an internal 32 MiB ceiling. Missing or
unusable lengths still receive the streamed check; the exact boundary succeeds.
Return a buffered `httpx.Response` so existing mixin consumers retain their
status, headers, text and JSON interface. Own source closure explicitly, shield
the close task from repeated cancellation and await its completion. Preserve an
in-flight failure/cancellation when close fails, attaching bounded secondary context.

Request `Accept-Encoding: identity` and reject non-identity content encoding
before reading. The operator explicitly selected this contract for #770: httpx's
automatic decompression occurs before its decoded iterator yields.

Independently cap HMCError's retained diagnostic body to 4 KiB of UTF-8 before
XML parsing. Oversize transport errors name the limit and declared or observed
size. Do not include an unbounded header value in those errors.

## Consequences

Successful responses above 32 MiB and compressed responses now fail explicitly.
#771 supplies the operator override. Text/JSON/XML processing happens only after
the byte bound; their existing semantics remain. The byte ceiling is not a cap
on total process RSS: transport chunks, bounded buffer copies and parsed models
also occupy memory. A mutation may already have occurred when its response fails;
the client does not retry it. Truncated diagnostics may no longer be valid XML.

## Considered & rejected

- **Keep eager transport and check text afterward.** verified: at source
  `11892cc`, `_request` calls `httpx.AsyncClient.request`; an in-process MockTransport
  probe declared 104857600 bytes and consumed all three chunks before returning.
- **Check each mixin separately.** judgment: duplication leaves the earliest
  materialization unprotected and adds maintenance to existing central routing.
- **Incrementally decompress.** judgment: a custom compression layer adds
  unnecessary complexity under the operator-selected identity-only contract.
- **Leave diagnostics at the transport ceiling.** judgment: retaining tens of
  MiB in an exception is unnecessary for diagnosis.
