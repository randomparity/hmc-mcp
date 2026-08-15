# HMC REST Session Reuse Recommendation

**Issue:** [#155](https://github.com/randomparity/hmc-mcp/issues/155)  
**Decision:** [ADR 0028](../../adr/0028-process-local-hmc-session-token-reuse.md)  
**Recommendation:** Proceed with process-local session-token reuse in a separate issue.

## Scope

This spike decides whether the measured cost of the current Logon → operation →
Logoff lifecycle justifies a future session-reuse implementation. It does not
change runtime code. The decision covers session lifetime and invalidation,
concurrent-session pressure, profile isolation, event-loop constraints, and the
safe replay boundary after authentication failure.

## Evidence

### Current implementation

- `HMCClient.__aenter__` logs on and `__aexit__` logs off and closes its
  `httpx.AsyncClient`.
- MCP tools enter their coroutine through `_run`, which calls `asyncio.run` for
  each synchronous tool invocation. An `AsyncClient` created by one invocation
  therefore must not be shared with another invocation's event loop.
- `client_from_env(profile)` resolves per-call routing. ADRs 0008 and 0009 make
  profile isolation a public routing invariant.
- The client currently treats a 401 like any other non-success response; it has
  no session invalidation or reauthentication path.

### IBM session behavior

IBM documents `/rest/api/web/Logon` PUT as creating an authenticated session,
returning an `X-API-Session` token that subsequent calls must send, and DELETE as
closing that session [1]. HMC REST and GUI sessions share configurable idle and
session timeouts and a configurable maximum web-session count [2]. IBM's
PowerVC guidance recommends an explicit per-user maximum and idle timeout after
401 failures associated with session count and timeout settings [3]. IBM also
documents that unclosed REST sessions can accumulate until the HMC refuses new
REST calls [4].

The documentation does not promise one universal token lifetime or session cap;
both are operator/HMC configuration. A future client must discover invalidation
from the response rather than embedding a guessed duration or limit.

### Representative measurement

An operator measured HMC V10R3 SP1060 from a macOS arm64 workstation on the
same LAN, using HTTPS on port 12443 with certificate verification disabled.
The procedure used one connectivity cycle, three discarded warm-up cycles,
then 20 sequential cycles. Each retained cycle created a fresh
`httpx.AsyncClient`, timed Logon and Logoff separately with `time.monotonic`,
and immediately logged off. All 24 sessions were reported closed; all retained
cycles succeeded, and no token was persisted.

| Metric | Reported | Recalculated from 20 posted samples |
|---|---:|---:|
| Logon median | 562.7 ms | 562.65 ms |
| Logon p95 | 649.3 ms | 649.21 ms |
| Logoff median | 182.0 ms | 182.0 ms |
| Logoff p95 | 237.0 ms | 236.85 ms |

The recalculation uses Python `statistics.median` and the exclusive p95 from
`statistics.quantiles(samples, n=100, method="exclusive")[94]`. The roughly
745 ms median Logon-plus-Logoff tax is material in a multi-tool agent workflow.

The issue comment names `scripts/measure_logon_latency.py`, but that path is not
present in `origin/main` or local Git history as of 2026-08-15. The raw samples,
procedure, environment description, and cleanup result are independently
sufficient to review and repeat the experiment; this document does not claim
that the named script is available.

## Reproducible measurement procedure

1. Use a representative non-production HMC profile and obtain authorization to
   create and immediately close repeated sessions.
2. Record the HMC software version and a public-safe description of the client
   and network path. Never record credentials, endpoints, or session tokens.
3. Run one connectivity Logon/Logoff cycle and three discarded warm-up cycles.
4. Run 20 sequential cycles. For each cycle, create a fresh `AsyncClient`, time
   `HMCClient.logon()` and `HMCClient.logoff()` separately with a monotonic
   clock, close the client, and retain only elapsed milliseconds and status.
5. Abort on a failed Logoff and reconcile the open session before continuing.
6. Verify no measurement-created sessions remain. Report successes, failures,
   median, and the p95 method for Logon and Logoff separately.

Concurrency is intentionally excluded: it would mix authentication latency
with HMC load and session-cap behavior.

## Recommendation

Proceed with a future, separately scoped implementation of a process-local
session-token cache. Keep each `httpx.AsyncClient` local to the tool call's
event loop. Cache only the opaque token and its route identity; never persist a
token to disk.

The cache key must include the caller-selected profile and the effective HMC
endpoint and user. Calls for different profiles must never share a token, even
when their current configuration happens to resolve to the same endpoint.
Logon creation for a key must be serialized so concurrent misses create at most
one session. The cache must hold at most one live token per key and explicitly
log off cached sessions during orderly process shutdown.

A 401 invalidates the token. Read-only/idempotent requests may perform one
serialized re-logon and one replay. Mutating requests must not be replayed after
a 401 because the client cannot prove that the HMC did not apply the operation;
they must return an actionable authentication-expired error. No retry loop is
permitted.

Credential-only changes do not change the cache key, and the evidence does not
show that HMC password rotation revokes existing tokens. Operators requiring
immediate revocation must restart `hmc-mcp` or use a future explicitly
authorized invalidation interface. Authentication material must never become
part of the key.

The design does not assume a fixed lifetime or cap. Operator-configured HMC
timeouts remain authoritative. Worst-case retained sessions equal the number of
distinct profile/route keys touched by each process, multiplied by the number
of processes using that HMC user. When a later call for the same profile
observes an effective-route change, it must evict and log off the displaced
token; profile deletion without another call is only cleaned up by orderly
shutdown or HMC-side invalidation. A session-limit rejection must not publish a
cache entry. Operators must size the HMC maximum for their deployment. A
universal process cap would be an unsupported guess because both the HMC cap and
deployment topology are configurable.

## Concurrency and lifecycle invariants

- Token state is process-local; transport objects remain event-loop-local.
- A synchronization primitive usable across separate `asyncio.run` calls guards
  each key's token and logon transition; an asyncio lock must not cross loops.
- Only the cache owner may replace or log off a cached token.
- A failed or cancelled logon publishes no token.
- Shutdown makes the cache unavailable before attempting best-effort Logoff, so
  no new caller can acquire a token being closed.
- Cleanup failure is reported and the local token is discarded; a stale local
  reference must not be reused.

## Threat model for future implementation

### Boundaries and actors

The MCP caller controls `profile`; the local operator controls profile files and
environment configuration; the HMC controls token validity and HTTP responses.
The future cache adds shared in-process credential state between calls but does
not widen the stdio-only deployment boundary.

### Controls

- Profile plus effective endpoint/user keying prevents cross-route credential
  reuse.
- Tokens remain memory-only and must never appear in logs, errors, metrics, or
  persisted measurement output.
- A per-key single-flight logon and one-token bound limit session creation.
- One retry for reads prevents loops; no mutation replay prevents ambiguous
  duplicate side effects.
- Fresh per-call clients retain existing TLS and audit-header configuration and
  avoid cross-event-loop transport use.

### Explicitly out of scope

This spike does not implement cache storage, shutdown hooks, reauthentication,
new configuration, telemetry, or session-cap discovery. It does not fix the
operator's choice to disable TLS verification. Those belong to the future
implementation and its tests/security review.

## Alternatives

1. **Persistent background event loop and shared `AsyncClient`.** Rejected for
   the first implementation because it rewrites execution and shutdown
   ownership when token-only reuse captures the measured authentication cost.
2. **Keep per-call Logon/Logoff.** Rejected because the measured median overhead
   is about 745 ms per tool call, or about 3.7 seconds across five calls, before
   useful HMC work.
3. **Persist tokens across processes.** Rejected because the token is an
   authentication credential and persistence adds exposure, ownership, and
   stale-token cleanup problems not needed for process-local amortization.

## References

1. IBM, [Logon and Logoff](https://www.ibm.com/docs/en/power11/9824-22A?topic=apis-logon-logoff).
2. IBM, [HMC settings](https://www.ibm.com/docs/en/power11/9824-42A?topic=tasks-hmc-settings).
3. IBM Support, [HMC session settings might cause deploy, LPM, or resize failure](https://www.ibm.com/support/pages/node/7185326).
4. IBM Support, [HMC REST API Logout to Close Sessions](https://www.ibm.com/support/pages/hmc-rest-api-logout-close-sessions).
