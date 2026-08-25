# ADR 0093: Cross-process job-polling contract

## Status

Accepted (2026-08-25)

## Context

Every long-running HMC operation returns a job, and the package has three ways to learn what
happened to one. None is usable by a supported library consumer:

- `jobs.wait_for_submitted_job` (`src/hmc_mcp/jobs.py:210`) lives in `jobs.py`, which ADR 0029's
  selection rule does not reach — the rule governs `operations_*.py` modules
  (`docs/adr/0029-supported-reusable-python-api-contract.md:47`).
- `HMCClient.get_job` (`src/hmc_mcp/client.py:904`) and `HMCClient.wait_for_job` (`:931`) are
  inherited mixin methods outside the supported lifecycle allowlist, which ADR 0029 fixes at
  exactly `__init__`, `__aenter__`, `__aexit__`, `is_logged_on`, `logon`, and `logoff` (`:39-45`).
  They remain callable, but "may change without a compatibility release".
- `JobOutcome` (`src/hmc_mcp/jobs.py:50`), the normalized result ADR 0081 classifies, is not
  exported.

The `wait=True` parameter that the operations and MCP tools carry is not a substitute. It holds one
open coroutine for the life of the job, so the job's identity exists only in the memory of the
process that submitted it. A worker that restarts mid-job has lost the job. The downstream consumer
(Bunson) runs NIM installs of 30–90 minutes and must survive a worker restart: the identifier has to
be recoverable from Postgres and pollable by a different process than the one that submitted it.

Polling from a restarted worker also exposes a failure the current code cannot express. The HMC
reaps job resources, so an identifier read back from a database may no longer resolve.
`HMCClient.get_job` turns that into `HMCError("GET /rest/api/uom/jobs/{id} failed", 404, ...)`
through the generic `_web_get` path (`client.py:708-717`) — indistinguishable, at the call site,
from any other transport failure. A restarted worker cannot tell "still running" from "gone".

## Decision

### 1. Two operations in `operations_jobs`

`src/hmc_mcp/operations_jobs.py` owns exactly two presentation-neutral asynchronous operations,
both exported from `hmc_mcp.api`:

```python
async def get_job(hmc, job_id, *, job_href=None) -> JobOutcome
async def wait_for_job(hmc, job_id, *, job_href=None, timeout_seconds=300, poll_interval=5) -> JobOutcome
```

They take an injected `HMCClient` like every other operation, and they are ordinary coroutines: an
in-process consumer awaits them from inside its own running event loop. Neither routes through
`_app._run`, which calls `asyncio.run` and therefore cannot be called from a running loop.

### 2. The job handle is two strings

The supported handle a caller persists is `job_id`, plus an optional `job_href`. Both are plain
strings, both are parameters of both operations, and both are fields of the returned `JobOutcome`.
That is the whole portability requirement: a process reads the two strings back from storage,
constructs a fresh `HMCClient`, and polls. No job object, no live coroutine, and no client instance
survives from submission.

`job_href` is optional because the documented global path `/rest/api/uom/jobs/{id}` resolves the
identifier on ordinary firmware. It exists because some firmware cannot (issue #95), and the
submission's SELF link is the only thing that works there. `HMCClient.get_job` already accepts it,
and `client._reject_non_job_path` (`client.py:87`) constrains it to a job resource path before any
request is made; that check is what bounds the parameter, and this ADR does not widen it.

### 3. `JobOutcome` is a package-owned model contract

`JobOutcome` is exported and its fields are supported under ADR 0029 — unlike the `job` field it
carries, which stays an opaque HMC resource mapping whose keys, nesting, and firmware-dependent
extensions are explicitly not a package contract (ADR 0029 `:102-106`). Consumers read `status`,
`timed_out`, `error`, `found`, `job_id`, and `job_href`; they read `job` at their own risk.

Two fields are new: `found` (clause 4) and `job_href` (clause 2). Neither carries a default. The
repository already tests that this result's MCP output schema has `required` equal to its full
property set — `hmc_wait_for_job` and the LPM recovery tools are asserted to share one stable wait
shape — and a defaulted field would drop out of `required` while still being serialized on every
response. Every outcome therefore states both facts explicitly. The ADR 0081 classification of
terminal statuses is untouched.

### 4. A job the HMC no longer knows about is `found=False`, not an exception

`get_job` reports a missing job as `JobOutcome(found=False, status=None, ...)`. This covers both
ways the HMC declines to produce the job: an HTTP 404, and a response carrying no job entry. The
translation lives in the operations layer, so `HMCClient.get_job` keeps raising and no other caller
of it silently loses an error.

The resulting distinction is the one a restarted worker needs:

| observation | meaning |
| --- | --- |
| `found=True`, `timed_out=False` | terminal; `status` and `error` describe the result (ADR 0081) |
| `found=True`, `timed_out=True` | the HMC still has the job and it has not reached a terminal status |
| `found=False` | the HMC does not know this identifier |

`found` is read first. `timed_out` reports only that no terminal status was observed, so a
vanished job carries `timed_out=True` as well; that value is not changed here because
`job_outcome(id, None)` already produces it and existing callers assert on it.

`found=False` deliberately does not say *why*. Reaped-after-completion, deleted, and never-existed
are the same observation over REST, and inventing a distinction the HMC does not report would be a
false contract. `error` stays `None`, because ADR 0081 reserves a non-empty `error` for an
actionable *terminal* outcome and a vanished job has no status to classify.

### 5. `wait_for_job` owns its poll loop

`wait_for_job` polls `get_job` and stops on the first of: a terminal status, `found=False`, or the
deadline. It does not delegate to `HMCClient.wait_for_job`, for two reasons. The stop condition
above is not expressible through that method — it raises on a 404 instead of returning, and keeps
polling until timeout when an entry is absent, which would make a vanished job block a restarted
worker for the full timeout. And ADR 0029 keeps client mixin methods unsupported and free to
change, so a supported operation must not inherit its timing semantics from one.

`timeout_seconds=0` performs exactly one poll, matching `validate_wait_timing` and the existing
`hmc_wait_for_job` tool. Timing arguments are validated by the same `jobs.validate_wait_timing`
every other operation uses; an empty `job_id` is rejected with `ValueError`.

### 6. Cancellation is safe by construction

Neither operation logs the injected client on or off, opens a session, or issues a write. Waiting
is a read followed by `asyncio.sleep`. Cancelling the waiting coroutine therefore propagates
`CancelledError` out of the sleep with no session mutation to unwind and no submitted work
abandoned mid-flight — the HMC-side job is unaffected by a client that stopped watching it, which
is the same property that makes polling from a second process possible at all.

### 7. Scope

Job polling is a read, so ADR 0092 §1 does not apply: that rule governs operations that change an
existing `LogicalPartition`'s existence, identity, configuration, resource shape, virtual-device
attachments, placement, or run state, and explicitly excludes read operations. No ownership guard
is added.

The synchronous helpers in `jobs.py` — `job_identifier`, `job_outcome`, and `validate_wait_timing` —
stay internal under ADR 0029's rule that a synchronous function is "a transformation, parser, or
validator rather than an asynchronous domain operation" (`:49-51`).

## Consequences

A consumer can persist `job_id` and `job_href` in one process and poll them from another with a
freshly constructed client, which is what unblocks worker-restart-tolerant long-running installs.
`JobOutcome`'s field set becomes a compatibility promise: adding, removing, or renaming a field is
now a minor release under ADR 0029, where before it was an internal edit.

ADR 0029's inventory gains an `operations_jobs` entry, and the mechanical facade-drift test added by
issue #363 now covers the module: both coroutines must stay exported or the test fails.

The residual gap is the *first* identifier. A caller that submits work through an existing operation
still receives the HMC's opaque job mapping and must read the identifier out of it; nothing in this
decision makes that read a supported contract. Closing it means changing what submitting operations
return, which is #361's submit-side work and a separate decision.

`found=False` is not proof that a job ran, failed, or ever existed. A consumer that needs to know
whether the work happened must observe the affected resource, not the reaped job record.

## Considered & rejected

**Raise a `JobNotFoundError` instead of returning `found=False`.** It is equally distinguishable,
but it splits one poll into two result channels and forces every caller of `wait_for_job` into a
`try/except` around a call that already returns a normalized outcome. It also reads as an error when
"the HMC reaped a completed job" is an ordinary, expected observation for a worker that restarted
hours later.

**Reuse `HMCClient.wait_for_job` and translate a 404 around the whole call.** Simpler by about ten
lines, and rejected in clause 5: it cannot stop early on an absent entry, and it makes a supported
operation's timing depend on a method ADR 0029 declares free to change.

**Return the raw job mapping like `HMCClient.get_job` does.** That would make IBM's open-ended
resource schema the consumer's parsing problem for the one result the issue exists to make legible,
and it has no place to put `found`.

**Require `job_href` alongside `job_id`.** It would force every consumer to persist a link that most
firmware does not need, and the submitting operation does not always return one.

**Accept the submission mapping instead of a bare identifier.** The mapping is the thing that cannot
be persisted and read back as a supported contract — its keys are explicitly not owned by this
package — so accepting it would defeat the portability the issue asks for.

**Add a `found` field to nothing and let `status=None` mean "gone".** `status=None` is already what a
job with an unparseable or absent `Status` produces, so overloading it would conflate a firmware
oddity with a reaped job and leave `timed_out=True` reading as "still running" in both cases.
