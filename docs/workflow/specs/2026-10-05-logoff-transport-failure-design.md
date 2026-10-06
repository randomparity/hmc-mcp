# Logoff transport failure after a completed body — design

Issue #1325. Governs `HMCClient.__aexit__` in `src/hmcpctl/client/core.py`; amends
[ADR 0028](../../adr/0028-process-local-hmc-session-token-reuse.md).

## Problem

`HMCClient.__aexit__` raises any logoff failure when the `async with` body exited cleanly. A
transient transport drop on `DELETE /rest/api/web/Logon` therefore fails a tool call whose
request already succeeded (observed once on a V11R2 HMC during #627; a re-run passed).

## Decision

Operator decision, campaign plan 2026-10-05: issue option 2.

| Body | Logoff outcome | `__aexit__` behaviour |
|---|---|---|
| clean | `HMCTransportError` (connect, read, protocol, timeout) | one WARNING on the `hmcpctl.client.core` logger naming the failure and that the HMC session may persist until the HMC times it out; no exception |
| clean | `HMCError` that is not `HMCTransportError` (non-2xx rejection) | raised, as today |
| clean | any other exception, including `CancelledError` | raised, as today |
| raised | any | unchanged: body exception is primary, cleanup failure attached as a note |

The HTTP client is closed on every row. A close failure keeps its current handling: after a
clean body with a logged logoff transport failure, the close error is raised. `logoff()` is
unchanged: it still raises both kinds and clears the token and `X-API-Session` header in its
`finally`. Only `__aexit__` narrows. The `isinstance` test must check `HMCTransportError`, which
subclasses `HMCError`.

ADR 0028 gains an `## Amendment (2026-10-05, #1325)` section recording the rule above. Its
Decision and its consequence that `logoff()` validates the response status both stand: the
amendment narrows only what the context manager propagates.

## Failure model

1. **Actors and deployments** — the MCP server and `hmcpctl` CLI, each tool call opening one
   `HMCClient` context; library consumers of the ADR 0118 facade using `async with HMCClient`.
2. **Invariants and assets at stake** — HMC web-session capacity (a leaked session holds a slot
   until the HMC timeout); the published error contract of `HMCClient` (ADR 0118); a completed
   mutation must not be reported as failed in a way that invites a duplicate retry.
3. **Accepted failure classes** — a logoff that the HMC actually did not process after a
   transport drop leaves one session until the HMC's own timeout: accepted, bounded by the
   HMC's configured session timeout and stated in the WARNING; logoff retry is excluded by the
   operator (owner: none). A library consumer that relied on catching the transport error from
   `__aexit__` no longer sees it: accepted, the change is recorded in `CHANGELOG.md`.
4. **Covered elsewhere** — session reuse and cache quarantine on ambiguous logoff: ADR 0028's
   future implementation, unchanged by this amendment; log sink routing: ADR 0043.

## Validation

| Contract | Mode | Evidence |
|---|---|---|
| clean body + logoff transport failure → no raise, one WARNING, client closed | focused-test | respx `DELETE` raising `httpx.RemoteProtocolError`; `caplog` on `hmcpctl.client.core` |
| clean body + logoff non-2xx → `HMCError` raised | focused-test | respx `DELETE` answering 500 |
| body raised + logoff transport failure → body exception primary with note, no WARNING | focused-test | respx `DELETE` raising; body raises `ValueError` |
| clean body + logoff transport failure + close failure → close error raised | focused-test | existing parametrized exit test, new row |
| `logoff()` still raises both kinds and clears state | focused-test | existing `test_logoff_*` tests, unchanged |
| ADR 0028 amendment, CHANGELOG entry | task-test-not-applicable | prose; no executable consumer reads either body |
