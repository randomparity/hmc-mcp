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

The exact retained samples, in milliseconds, are:

```text
Logon:  593.6, 627.6, 622.4, 647.4, 606.2, 579.2, 602.7, 649.3, 603.0, 580.3,
        546.1, 488.1, 481.0, 478.4, 485.3, 483.6, 480.9, 503.0, 462.1, 467.9
Logoff: 220.6, 222.4, 196.9, 212.2, 219.2, 199.8, 237.0, 233.9, 217.1, 183.4,
        178.4, 169.8, 173.4, 180.6, 178.4, 164.9, 160.6, 177.3, 162.6, 160.0
```

The recalculation uses Python `statistics.median` and the exclusive p95 from
`statistics.quantiles(samples, n=100, method="exclusive")[94]`. The roughly
745 ms median Logon-plus-Logoff tax is material in a multi-tool agent workflow.

The [exact measurement comment](https://github.com/randomparity/hmc-mcp/issues/155#issuecomment-5302760125)
is the raw evidence source. It names `scripts/measure_logon_latency.py`, but that
path is not present in `origin/main` or local Git history as of 2026-08-15. The
raw samples, procedure, environment description, cleanup result, and executable
equivalent below are independently sufficient to review and repeat the
experiment; this document does not claim that the named script is available.

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

Save the following as a temporary file and run it with `uv run python FILE
PROFILE`. It deliberately emits only aggregate timings and never emits the
profile, endpoint, credentials, or tokens.

```python
import asyncio
import statistics
import sys
import time

from hmc_mcp.api import HMCClient
from hmc_mcp.common import build_config


async def cycle(profile: str) -> tuple[float, float]:
    client = HMCClient(build_config(profile))
    entered = False
    try:
        start = time.monotonic()
        await client.__aenter__()
        entered = True
        logon_ms = (time.monotonic() - start) * 1_000
        start = time.monotonic()
        entered = False
        await client.__aexit__(None, None, None)
        return logon_ms, (time.monotonic() - start) * 1_000
    finally:
        if entered:
            await client.__aexit__(None, None, None)


async def main(profile: str) -> None:
    await cycle(profile)  # connectivity
    for _ in range(3):
        await cycle(profile)  # discarded warm-up
    samples = [await cycle(profile) for _ in range(20)]
    for index, name in enumerate(("Logon", "Logoff")):
        values = [sample[index] for sample in samples]
        p95 = statistics.quantiles(values, n=100, method="exclusive")[94]
        median = statistics.median(values)
        print(f"{name}: n=20 median={median:.2f}ms p95={p95:.2f}ms")


try:
    asyncio.run(main(sys.argv[1]))
except Exception as exc:
    print(
        f"measurement failed ({type(exc).__name__}); reconcile HMC sessions",
        file=sys.stderr,
    )
    raise SystemExit(1) from None
```

If any cycle raises, the run is failed even if the `finally` Logoff succeeds;
reconcile the HMC session inventory before repeating it. After a successful
run, independently confirm that no measurement-created session remains. Do not
copy raw exception objects or tracebacks into the evidence record because they
can contain the endpoint or server response text.

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
Configuration resolution and route replacement must be serialized by profile
selector (the explicit profile name or a default-selection sentinel). A caller
resolves configuration only after acquiring that selector lock, compares it
with the current route generation, and retires the prior route before a new
per-route Logon begins. A waiting caller re-resolves after acquiring the lock,
so a stale snapshot captured before a newer route cannot later win. Logon
creation for a route key is serialized so concurrent misses create at most one
session. The cache must hold at most one published or remotely live token per
key during successful operation and explicitly log off cached sessions during
orderly process shutdown.

A 401 invalidates the token. Only request definitions on an explicit, reviewed
replay allowlist may perform one serialized re-logon and one replay. Allowlist
membership means the operation is safe to repeat after an ambiguous response;
an HTTP verb or tool description alone is not evidence of that property. Every
absent or unknown classification defaults to non-replayable. Mutating and
unclassified requests return an actionable authentication-expired error because
the client cannot prove the HMC did not apply the operation. No retry loop is
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
- A cross-loop selector lock covers effective-route resolution, route-generation
  comparison, and retirement. A stale concurrent route snapshot cannot publish.
- A synchronization primitive usable across separate `asyncio.run` calls guards
  each key's token and logon transition; an asyncio lock must not cross loops.
- Each published token has a monotonically changing per-key generation and an
  active-borrower count. A request releases the same generation it acquired.
- A 401 may invalidate only the generation used by that request. A delayed 401
  for an older generation cannot evict its replacement. The request observing
  the 401 is itself a borrower: while holding the key transition lock, it must
  atomically retire the matching generation and release its own lease exactly
  once before awaiting the remaining borrowers. Cancellation at any point in
  that transition must not double-release the lease or leave it held. After the
  atomic transition, cleanup responsibility belongs to process-owned per-key
  state rather than to the observer task. Cancellation cannot abandon that
  responsibility. If cancellation occurs before Logoff dispatch, a later owner
  resumes drain and cleanup. Cancellation during or after Logoff dispatch is an
  ambiguous cleanup failure and quarantines the key with the actionable
  reconciliation error. After the remaining active borrowers drain, the
  rejected generation must pass the same validated Logoff contract before one
  replacement Logon may proceed; rejection does not prove that the HMC removed
  the remote session. An allowlisted replay acquires the replacement generation
  and a new lease rather than reusing the retired generation's released lease.
- Replacement, route-change eviction, and shutdown mark a generation retired;
  Logoff is deferred until its active-borrower count reaches zero. New callers
  cannot acquire a retired generation, and no replacement Logon may begin until
  that generation drains and its cleanup outcome is known.
- Explicit Logoff succeeds only on HTTP 200, 202, or 204, the success statuses
  identified by IBM's Logoff guidance. `HMCClient.logoff()` must validate that
  response instead of clearing the token for every status as it does today.
  Successful Logoff permits one replacement Logon. Every other HTTP status,
  malformed response, timeout, cancellation, or transport failure is ambiguous
  and quarantines the key: it publishes no replacement and returns an actionable
  reconciliation error, preventing repeated failures from accumulating remote
  sessions.
- Quarantine lasts for the process lifetime. After reconciling the remote HMC
  session, restarting `hmc-mcp` is the supported recovery action, and the error
  must state both steps. In-process quarantine clearing is not part of the first
  implementation.
- A failed or cancelled logon publishes no token.
- Cancellation after acquisition releases its borrower count exactly once and
  cannot publish, evict, or close a newer generation.
- Shutdown makes the cache unavailable before attempting best-effort Logoff, so
  no new caller can acquire a token being closed. It waits at most 30 seconds
  for active borrowers. If the deadline expires, it reports the outstanding
  borrower count, discards local token state, never forces Logoff beneath an
  active mutation, and leaves remaining remote cleanup to HMC invalidation.
- Thirty seconds bounds borrower drain, not all orderly-shutdown work. After a
  successful drain, Logoff calls for independent keys run concurrently and each
  remains bounded by the existing HTTP request timeout. Cleanup failures are
  reported; shutdown does not retry them.
- Cleanup failure is reported and the local token is discarded; a stale local
  reference must not be reused.

A future implementation must deterministically test concurrent read/read reuse,
a delayed 401 from an older generation, route change during an in-flight call,
shutdown during active use, repeated invalidation while borrowers remain active,
ambiguous cleanup failure, and cancellation during Logon and after acquisition.
One single-borrower 401 test must prove that the observer retires the matching
generation, releases its own lease exactly once, and reaches cleanup without
waiting on itself; an allowlisted replay must then acquire a new generation and
lease. Cancellation tests must cover cancellation before and after retirement,
lease release, cleanup, and replacement acquisition, proving no leaked or
double-released lease and no publication, eviction, or cleanup of a newer
generation. Each cancellation boundary must reach exactly one terminal state:
validated cleanup followed by eligibility for one replacement acquisition, or
process-lifetime quarantine with the actionable reconciliation error. A
pre-dispatch cancellation must prove that a later owner resumes cleanup; a
during- or post-dispatch cancellation must prove quarantine.
The shutdown test must cover both drain before 30 seconds and deadline expiry
with an outstanding mutation borrower, including the reported count and absence
of a forced Logoff. It must also cover multiple slow Logoffs running concurrently.
Replay tests must prove every existing request definition is classified, unknown
classification is non-replayable, and allowlisted requests replay at most once.
A two-route race test must prove that a stale route cannot publish or remain
cached after a newer selector-locked resolution. Cleanup tests must prove that
200, 202, and 204 permit replacement; every other 4xx/5xx, malformed response,
timeout, cancellation, and transport failure quarantines; and a generation
rejected with 401 cannot be replaced unless its subsequent validated Logoff
succeeds. Repeated 401 tests must prove remote sessions cannot accumulate.

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
- At most one retry for explicitly allowlisted operations prevents loops; HTTP
  verb and read-like naming are insufficient, and absent classification is
  non-replayable.
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
