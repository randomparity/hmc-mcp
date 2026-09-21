# Bounded HMC REST responses

## Problem and authority

#770, under #768, owns transport enforcement. The frozen scope is
`q770-16b93c86` on #770. The operator approved its exclusions and selected
identity-only content encoding. ADR 0133 records that decision.

## Scope and design

Keep `_request(method, path, **kwargs) -> httpx.Response` as the shared entry
point. UOM, web, logon/logoff, raw, job, and direct template/user/storage/PCM/update
consumers already reach it; no new mixin API is needed. Use `build_request` and
`send(stream=True)`, with an internal `MAX_RESPONSE_BYTES = 32 * 1024 * 1024`.
Request identity encoding even when the request supplies headers. Reject a
nonempty Content-Encoding other than case-insensitive identity before reading.

For an ASCII decimal Content-Length, compare the normalized decimal string to
the limit before integer conversion, so arbitrarily long decimal headers cannot
evade checking or expand diagnostics. Missing, negative, malformed or conflicting
lengths are unusable; count bytes for them. Count bytes for usable lengths too,
so a dishonest small declaration does not bypass enforcement. Stop at the first
chunk crossing the ceiling, before retaining it or requesting another chunk.
The exact boundary succeeds. Empty bodies remain empty.

Construct a buffered response with the original status, headers, request, and
extensions after collection. Keep existing timeout/transport error normalization.
Iterate the response's public AsyncByteStream directly after the encoding check,
so httpx does not implicitly close it during iteration. In a finally block, own
one response-close task, shield it from cancellation, and keep awaiting that task
when repeated cancellation arrives. Propagate cancellation after closure. Preserve
an in-flight overflow/read/cancellation exception if close fails, attaching a
bounded secondary note; a standalone close failure propagates normally. HTTP
status interpretation remains with existing callers.

In `errors.py`, truncate diagnostic body to at most 4096 UTF-8 bytes before
storing or XML parsing, slicing characters before encoding to avoid encoding a
complete large body. This also bounds diagnostics attached to unsuccessful
semantic responses with a successful HTTP status. Preserve small-body behavior.

## Success

- The named client paths reject declared overflow before requesting a body chunk.
- Streamed overflow names observed bytes and the limit, consumes no later chunk,
  and applies whether the declared length is usable, absent or misleading.
- Exact-limit text/JSON and empty success responses retain expected decoding.
- Oversized HTTP failures cannot retain diagnostics beyond the independent cap.
- Compressed replies fail before consumption; requests advertise identity only.
- Source responses close on success, overflow, transport failure and cancellation.

## Failure model

- Actors and deployments: library, CLI and MCP callers using the async HMC client;
  remote HMC services and intermediary responses are untrusted byte producers.
- Invariants and assets: bounded body buffering, bounded diagnostics, session
  connection release, established response decoding and transport error categories.
- Accepted failure classes: total RSS can exceed the body ceiling because bounded
  copies, a transport-provided chunk and parsed objects also consume memory;
  already executed remote mutations are not rolled back when response reading fails;
  a transport close that itself fails is attempted and reported, not falsely
  claimed successful. Process termination cannot execute asynchronous cleanup.
- Covered elsewhere: configuration sources/docs → #771; ISO-download redesign →
  existing bounded download path; parser/schema redesign → future domain issue;
  server-side listing limits → #768. Existing request timeout governs slow peers.

## Threat model

- Boundary inventory: existing HMC HTTP response headers/body entering client
  memory and diagnostics; no new entry points or wider authorization.
- Actor model: HMC or intermediary can choose lengths, encoding, status and chunks.
  Installed transport and local Python runtime remain trusted.
- Controls: pre-read decimal length and encoding checks, cumulative raw byte count,
  independent diagnostic truncation, stream lifetime management. Error messages
  expose only bounded diagnostic text and bounded size metadata.
- Out of scope: compromised local runtime, parser/schema redesign and existing
  credential/authorization policy; none is weakened by this transport change.

## Validation and global constraints

Python >=3.11; declared targets amd64 and arm64; no new dependencies or config fields.
Use a real httpx AsyncClient with injected AsyncByteStream/MockTransport to observe
body consumption and closure. Tests cover length grammar, exact limits, misleading
lengths, status failures, compression rejection, read errors, close failures and
repeated cancellation during a gated close.
Exercise direct consumers and verify a controlled bypass restores the old failure.
Run focused pytest without coverage, `just verify`, and
`uv run --no-sync prek run --all-files`; hosted CI covers Python 3.11–3.14 on both
architectures. No live HMC is required to exercise malicious transport replies.
