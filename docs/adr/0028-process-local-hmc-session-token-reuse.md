# ADR 0028: Process-Local HMC Session-Token Reuse

## Status

Accepted

## Context

Every MCP tool call currently creates an `HMCClient`, logs on, performs the
operation, logs off, and closes its HTTP client. The issue #155 measurement on
a same-LAN HMC V10R3 SP1060 recorded 20 successful cycles with no failures: a
562.7 ms median and 649.3 ms p95 Logon, plus a 182.0 ms median and 237.0 ms p95
Logoff. Every measured session was closed. The linked
[research recommendation](../workflow/specs/2026-08-15-hmc-session-reuse-recommendation.md)
records the procedure, independent recalculation, execution context, raw
evidence location, and the absent-script caveat. The median authentication
overhead is about 745 ms per tool call.

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
endpoint/user route. Cross-loop synchronization first serializes configuration
resolution and route replacement by profile selector, then serializes Logon per
route key. A waiting caller re-resolves configuration after acquiring the
selector lock, so a stale route snapshot cannot publish after a newer one. The
cache holds at most one published or remotely live token per key during
successful operation, never persists tokens, and explicitly logs off cached
sessions during orderly shutdown. Replacement waits for active borrowers of the
retired token to drain and for cleanup to complete. An ambiguous cleanup failure
quarantines the key and blocks replacement Logon until operator reconciliation,
preventing repeated failures from accumulating remote sessions. Quarantine
lasts for the process lifetime; after reconciling the HMC session, process
restart is the supported recovery action.

Orderly shutdown stops new acquisitions and gives active borrowers a fixed
30-second drain deadline. If borrowers remain, it reports their count, discards
local token state, and leaves their remote sessions to HMC invalidation. It
never forces Logoff beneath an active mutation. If borrowers drain, independent
Logoff requests run concurrently and retain their existing per-request timeout;
the 30-second promise applies to drain, not subsequent cleanup duration.

Profile isolation means the process-wide count is not a fixed constant: its
worst case is one retained session for every distinct profile/route key touched.
Multiple `hmc-mcp` processes multiply that count for the same HMC user. The
future implementation must log off and evict the prior token when a later call
for the same profile observes an effective-route change, and must surface an
HMC session-limit rejection without retaining a new entry. Profile deletion
without another call is not observable in this design; its token remains until
orderly shutdown or HMC-side invalidation. Operators remain responsible for
sizing the HMC's configured maximum web sessions above the profiles and
processes they run. No universal client-wide cap is chosen because the HMC cap
and deployment topology are operator-configured and no safe universal value is
documented.

The client assumes no fixed token lifetime. A 401 evicts the matching token.
Only request definitions on an explicit reviewed allowlist may perform one
serialized re-logon and one replay; classification means safe after an ambiguous
response and never derives from HTTP verb alone. Missing or unknown
classification is non-replayable. Mutating and unclassified requests return an
actionable authentication-expired error because the client cannot prove the
original operation had no effect.

A credential-only profile change does not alter the cache key, and no cited HMC
documentation proves that credential rotation immediately revokes existing
tokens. Operators must restart `hmc-mcp`, or use a future explicitly authorized
cache-invalidation interface, when rotating credentials and immediate session
revocation is required. Authentication material must not be added to the key.

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
- Replacement can wait for in-flight callers, and an ambiguous cleanup failure
  makes that key unavailable until the operator reconciles the HMC session and
  restarts the process.
- Borrower drain during shutdown is bounded to 30 seconds. Deadline expiry can
  leave remote sessions until HMC invalidation, but cannot interrupt an active
  mutation by logging off its session. Subsequent concurrent Logoff requests
  retain the configured per-request timeout.
- A process that touches many profiles can retain many sessions; deployments
  must include every active process when comparing demand with the HMC's
  per-user maximum.
- Unexpected process termination may leave a session until HMC invalidation;
  orderly shutdown attempts explicit Logoff and reports cleanup failures.
- Mutating callers may need to inspect state and decide whether to retry after
  an authentication-expired error.
- Credential rotation may not affect an already-issued token until HMC
  invalidation or process restart; restart is the supported immediate-revocation
  action unless later authoritative HMC evidence establishes stronger behavior.

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
