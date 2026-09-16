# ADR 0149: One job path expression, guarded wherever it is built

## Status

Accepted (2026-09-15)

## Context

`HMCClient.get_job_entry` and `HMCClient.delete_job` build the same request path
from the same two arguments — `urlparse(job_href).path` when a SELF link is
supplied, `/rest/api/uom/jobs/{job_id}` otherwise — and then disagreed about it.
`delete_job` guarded the finished expression; `get_job_entry` guarded only its
`job_href` branch, and no docstring explained why (issue #825).

`_reject_non_job_path`'s own argument is about the constructed path, not about
which argument produced it: it binds the *resource class* the request addresses,
so an href for `/rest/api/uom/HmcUser/root` cannot be read through a tool
classified `read` on target kind `job`. A `job_id` reaches that same path, and
`job_id = "a/b"` yields `/rest/api/uom/jobs/a/b`, which leaves the job class
outright — not the residual ADR 0039 accepts, which is reaching a *different job*.

Separately, `_JOB_PATH` anchored its identifier segment as `[^/]+`, and `?` and
`#` are both `[^/]`. `job_id = "j?x=1"` matched, and httpx then split the path at
the `?` into a query this client never intended. Neither method refused it.

## Decision

**Both methods build the job path in one expression and refuse it through
`_reject_non_job_path`, and `_JOB_PATH` excludes `?` and `#` from every segment.**

The guard also takes the name of the argument the path was built from, so a
caller who passed only `job_id` is not told to pass a SELF link instead.

`_JOB_PATH` keeps matching the percent-decoded path only. `unquote` can introduce
`/`, `?` and `#` but never remove one, so the decoded form is the more refusing of
the two and matching the raw form as well would refuse nothing further.

## Consequences

- `get_job_entry` now refuses, before any request, a `job_id` that is empty or
  carries `/`, `?`, `#`, or a percent-encoding of one. It sent those before;
  `delete_job` already refused all but `?` and `#`.
- The two call sites are textually identical, which is what keeps them from
  drifting apart again.
- The gap was reachable only from the public `HMCClient` API:
  `src/hmc_mcp/operations/jobs.py:38` already refuses `/?#%` in a `job_id`, so
  `hmc_get_job`, `hmc_wait_for_job` and the CLI never reach this guard.
- Residual, unchanged: an `all-targets` grant still reaches a *different* job.
  ADR 0039 accepts that, and this does not revisit it.

## Considered & rejected

- **Record the asymmetry as intentional and leave the code alone.** judgment:
  `_reject_non_job_path`'s stated reason is about the path the request will use,
  which the `job_id` branch builds too, so the record would disagree with the
  guard it describes.
- **Add a character check for `job_id` beside the path check, mirroring
  `_ILLEGAL_JOB_ID_CHARACTERS`.** verified: `src/hmc_mcp/operations/jobs.py:38`
  holds that rule at the operations boundary already. judgment: `_JOB_PATH`
  anchors the identifier segment, so the same site would state one rule twice.
- **Bind the final segment to `job_id` itself.** verified: rejected already in
  `_reject_non_job_path`'s docstring — `jobs.job_identifier` prefers the
  response's `UUID`/`JobID` over the link's last segment, and issue #95 is
  firmware that cannot resolve the identifier at all. Unchanged here.
- **Match the raw path as well as the decoded one.** verified: `unquote` only
  ever adds characters, so over the set `_JOB_PATH` tests the decoded form
  refuses everything the raw form would. judgment: a second match that can change
  no outcome.
