# ADR 0093: Cross-process job-polling contract

## Status

Accepted (2026-08-25)

## Context

Every long-running HMC operation returns a job, and the package has three ways to learn what
happened to one. None is usable by a supported library consumer:

- `jobs.wait_for_submitted_job` (`src/hmc_mcp/jobs.py:219`) lives in `jobs.py`, which ADR 0029's
  selection rule does not reach — the rule governs `operations_*.py` modules
  (`docs/adr/0029-supported-reusable-python-api-contract.md:47-49`).
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

A supplied `job_href` therefore **decides which job is read** — `_reject_non_job_path` binds the
resource class, not the identifier, and its own docstring records the residual: "a caller may read
a *different* job". That residual was accepted for the MCP surface, where an ADR 0039 all-targets
grant already means "any job". Here the pair is read back from storage, where the two columns of
one row can be written out of step, so the residual becomes a mispaired handle returning another
job's terminal status. `get_job` does not raise on the mismatch: `jobs.job_identifier` prefers the
response's UUID or JobID over the link's last segment, so the two legitimately differ on some
firmware, and refusing would break the case `job_href` exists to serve. It logs a warning instead,
returns the **response-derived** `job_id` — which names the job actually read — and both the
docstring and this clause tell a consumer to compare `job_id` against what it stored before acting.

Rejected as too strong: raising on the mismatch. Rejected as too weak: returning the requested
identifier, which would hide the substitution entirely.

The comparison is **advisory**, and the ADR says so rather than leaning on it as the whole
mitigation. `jobs.job_identifier` prefers the response's `UUID` over its `JobID`, so a handle a
consumer stored as a JobID — which is what `hmc_wait_for_job`'s own docstring tells it to store —
differs from the returned `job_id` on firmware that reports both, with no substitution involved.
The warning therefore fires only when a `job_href` was supplied (without one the request path is
built from the identifier, so a differing label cannot mean a different job was read) and at most
once per `wait_for_job` call rather than once per poll — an hour at the default five-second
interval would otherwise emit some seven hundred identical lines and bury the signal.

A supplied link also carries a failure mode of its own. A per-operation SELF link embeds the
target resource, not just the job — `.../LogicalPartition/{uuid}/do/PowerOn/Job/{id}` — so it can
stop resolving while the job is fine, and this package ships decommission operations that remove
such parents. A 404 raised against a supplied link is therefore **confirmed against the global
jobs path** before it becomes `found=False`; when the second read finds the job, that result is
returned and the stale link is logged. The confirmation is best-effort: on firmware that does not
serve the global path it fails, and a failure leaves the original 404 standing rather than
replacing a documented `found=False` with an exception.

A link proved stale is then **dropped**, not echoed. The outcome carries the href from the read
that worked, so a consumer re-persisting the handle from every outcome never stores a link known
not to resolve; and a wait stops using the link for its remaining polls, so the confirming second
request and its warning happen once per wait rather than on all several hundred polls of a
multi-hour install — the same flooding this ADR designs against for the substitution warning.

### 3. `JobOutcome` is a package-owned model contract

`JobOutcome` is exported and its fields are supported under ADR 0029 — unlike the `job` field it
carries, which stays an opaque HMC resource mapping whose keys, nesting, and firmware-dependent
extensions are explicitly not a package contract (ADR 0029 `:109-113`). Consumers read `status`,
`timed_out`, `error`, `found`, `job_id`, and `job_href`; they read `job` at their own risk.

The type is shared, and the polling reading of its fields is scoped to this decision's two
operations. `jobs.job_outcome` has six other callers, all of them *submitting* operations
reporting a submission rather than a poll, and they do not satisfy the handle promise:
`operations_provision.py:284` and `operations_lpar.py:332` pass the literal `"PowerOn"` as the
identifier, and `operations_lpm.py:261` and `operations_decommission.py:466` fall back to `""`
when a submission returned no identifier. Their `found=False` means "this submission returned no
job entry", not "the HMC reaped it", and `operations_lpm._finish_job` pairs it with
`timed_out=False` for a fire-and-forget submission — a combination clause 4's table does not
cover, because that table describes a poll.

Making all seven producers satisfy one reading would mean rewriting five call sites and their
tests for no consumer that asked for it, so this ADR bounds the claim instead: the handle and
`found` semantics below apply to outcomes returned by `operations_jobs`, and both `JobOutcome`'s
docstring and the changelog say so. A consumer polls a `job_id` that came from a polling operation
or from the HMC's own submission response — never one a submitting operation labelled.

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

The resulting distinction is the one a restarted worker needs — for an outcome this decision's two
operations returned (clause 3 bounds the claim):

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

The translation keys on the status code alone, so it also absorbs a 404 that means "this
deployment does not serve that path" — a base URL, reverse proxy, or firmware level where the
global jobs path is absent, as distinct from issue #95's HTTP 400 REST000E, which
`_check_web_rest000e` already turns into an actionable error. The code cannot tell that apart from
a reaped job, and a deployment in that state answers `found=False` for every job forever. What it
can do is be loud: the translation logs at **warning**, naming the identifier, whether a
`job_href` was used, and the discarded `HMCError` detail. A `found=False` a consumer acts on
destructively is not an INFO-level event, and a systematically 404-ing deployment shows up in
ordinary logs rather than only under debug.

### 5. `wait_for_job` owns its poll loop

`wait_for_job` polls `get_job` and stops on the first of: a terminal status, `found=False`, or the
deadline. It does not delegate to `HMCClient.wait_for_job`, for two reasons. The stop condition
above is not expressible through that method — it raises on a 404 instead of returning, and keeps
polling until timeout when an entry is absent, which would make a vanished job block a restarted
worker for the full timeout. And ADR 0029 keeps client mixin methods unsupported and free to
change, so a supported operation must not inherit its timing semantics from one.

`timeout_seconds=0` performs exactly one poll, matching `validate_wait_timing` and the existing
`hmc_wait_for_job` tool. Timing arguments are validated by the same `jobs.validate_wait_timing`
every other operation uses; a `job_id` that is empty, that is a bare dot segment, or that carries a
path, query, or whitespace character and so would address something other than one job, is rejected
with `ValueError`. The dot-segment case is listed explicitly because the client's own
`_reject_dot_segments` would otherwise catch it one layer down and raise `HMCError`, breaking the
`ValueError` contract for exactly the input class that contract names.

A job that vanishes **during** a wait is not reported gone on one read. The 404 translation is the
only failure on this path that returns successfully instead of raising, so a momentary 404 — a
proxy reload, a failover pair whose standby has not surfaced the job — would otherwise be handed to
a consumer as a vanished install, and the re-call recovery above does not help because the caller
has already been told the job is gone. The wait re-reads once, one poll interval later, and accepts
`found=False` only when the second read agrees. This is not the retry clause 5 declines: that
paragraph is scoped to failures that propagate as `HMCError`, where the caller knows something went
wrong. A job missing from the *first* read is still reported immediately — there is no earlier
observation for it to contradict.

Once accepted, the disappearance is returned as a bare `found=False`. The status observed on
the poll before is not carried on the outcome: `found=False` means the HMC produced no entry, and
attaching a last-known status to it would contradict that and put a fourth row in clause 4's table.
The evidence is not discarded — the transition logs at warning with the last status seen — but a
consumer that must distinguish "ran for twenty minutes, then disappeared" from "never resolved"
polls in its own loop with `get_job`. Carrying it on the result would be better for that consumer
and is a change to what `found=False` means, so it belongs in a later decision, not here.

The loop has **no retry**. Any non-404 HMC failure — a 5xx, a network timeout, an expired session —
propagates as `HMCError` and ends the wait. That matters at the ADR's own motivating scale:
`HMCClient` performs no re-logon, so a wait sized to a 90-minute install will outlive the HMC's
session lifetime and die partway through. Building retry and re-logon into the operation is the
larger change and is not made here, because the design already survives the failure: the operation
is a pure read, and re-calling it with the same two strings resumes exactly where it stopped. What
was missing was saying so, which the `wait_for_job` docstring now does — size `timeout_seconds` to
a session, not to the job, and drive a longer wait by calling again.

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
whether the work happened must observe the affected resource, not the reaped job record — and
because the translation cannot distinguish a reaped job from a job path this deployment does not
serve, a `found=False` that repeats for every identifier is a configuration signal, not a fleet of
vanished jobs.

`found` and `job_href` also land in the MCP output schema that `hmc_wait_for_job` shares with every
submit-and-wait tool, because `jobs.job_outcome` is the one normalizer behind all of them.
`hmc_wait_for_job`'s tool docstring describes both fields — including that on *that* tool a reaped
job still surfaces as an `HMCError`, because it polls through `HMCClient.wait_for_job`, which
raises on the 404 these operations translate. The MCP surface does not gain the reaped-versus-
running distinction here; only `hmc_mcp.api` does. The submitting tools' own docstrings describe
neither field: five presentation docstrings are outside this decision's surface, so issue #456 owns
that pass. Until it lands, an agent reading `found` off a submission report has the tool docstring
of `hmc_wait_for_job` and this ADR, and nothing on the tool it actually called.

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
