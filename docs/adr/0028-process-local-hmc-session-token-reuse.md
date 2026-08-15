# ADR 0028: Process-Local HMC Session-Token Reuse

## Status

Accepted

## Context

Every MCP tool call currently creates an `HMCClient`, logs on, performs the
operation, logs off, and closes its HTTP client. A representative HMC V10R3
measurement recorded a 562.7 ms median Logon and 182.0 ms median Logoff across
20 successful sequential cycles: about 745 ms of authentication overhead per
tool call.

HMC session lifetime and maximum web sessions are configurable, and leaked
sessions can exhaust HMC resources. Per-call profile routing is an existing
safety invariant. Each synchronous MCP tool also uses its own `asyncio.run`, so
HTTP clients and asyncio synchronization primitives cannot safely be shared
across calls.

## Decision

A future implementation will reuse HMC session tokens within one `hmc-mcp`
process while retaining a fresh, event-loop-local `httpx.AsyncClient` for every
tool call.

The cache key includes both the caller-selected profile and the effective HMC
endpoint/user route. A per-key cross-loop synchronization mechanism serializes
Logon so a cache miss creates at most one session. The cache holds at most one
live token per key, never persists tokens, and explicitly logs off cached
sessions during orderly shutdown.

The client assumes no fixed token lifetime. A 401 evicts the matching token.
Read-only/idempotent requests may perform one serialized re-logon and one
replay. Mutating requests are not replayed after a 401; they return an
actionable authentication-expired error because the client cannot prove the
original operation had no effect.

The implementation is deferred to a separate issue with concurrency,
invalidation, shutdown, profile-isolation, redaction, and replay-boundary tests.

## Consequences

- Multi-call workflows avoid the measured Logon/Logoff tax while a token is
  valid.
- Profile identity remains part of routing even when two profiles currently
  resolve to the same endpoint and user.
- HTTP connection pooling is still per call; this decision amortizes HMC
  authentication, not transport setup.
- The one-token-per-key bound reduces session pressure but does not replace HMC
  operator timeout and maximum-session configuration.
- Unexpected process termination may leave a session until HMC invalidation;
  orderly shutdown attempts explicit Logoff and reports cleanup failures.
- Mutating callers may need to inspect state and decide whether to retry after
  an authentication-expired error.

## Considered & rejected

**Share one `AsyncClient` on a persistent background event loop.** This could
also preserve connection pools, but it changes execution, cancellation, and
shutdown ownership. Token-only reuse captures the measured authentication cost
without that broader runtime rewrite.

**Continue logging on and off for every call.** This keeps lifecycle ownership
simple but retains about 745 ms median overhead per call on the representative
HMC and compounds across agent workflows.

**Persist tokens across processes.** Tokens are authentication credentials.
Persistence introduces secret storage, stale-token ownership, and crash cleanup
without evidence that cross-process reuse is required.

**Automatically replay every request after reauthentication.** A mutating
request might have taken effect even if the client receives a 401 or loses the
response. Automatic replay would risk duplicate or conflicting side effects.

