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

Two gaps sat inside the pattern. Its identifier segment was `[^/]+`, and `?` and
`#` are both `[^/]`: on httpx 0.28.1 `build_request("GET", ".../jobs/j?x=1")`
sends `.../jobs/j` with query `x=1`, and `.../jobs/j#f` sends `.../jobs/j` with
the fragment dropped, so `delete_job("j#f")` silently deleted job `j`. And it was
matched against `unquote(path)` alone, which is not the more refusing form:
`unquote` introduces `/`, so a decode can *manufacture* the trailing `/Job/{id}`
the pattern looks for. `/rest/api/uom/HmcUser/root%2FJob%2Fx` decodes to a job
path and passed, while httpx put the undecoded raw path on the wire.

## Decision

**Both methods build the job path in one expression and refuse it through
`_reject_non_job_path`; `_JOB_PATH` must match the raw path *and* its
percent-decoded form; and its identifier segment excludes `?` and `#`.**

Matching both forms is what `_reject_dot_segments` already does in this module,
for the reason its own comment gives: checking one form is an assumption about
how the HMC decodes a path, and the wrong way round for a fail-closed check. Only
the identifier segment is tightened, because `urlparse` strips a query and a
fragment and the `job_id` branch's prefix is a literal, so no other segment can
carry a raw `?` or `#`. The guard also takes the name of the argument the path
came from, and its message says what each argument takes.

## Consequences

- `get_job_entry` now refuses, before any request, a `job_id` that is empty or
  carries `/`, `?`, `#`, or a percent-encoding of one. It sent those before;
  `delete_job` already refused all but `?` and `#`.
- A `job_href` is newly refused when its path becomes a job path only after
  decoding (`/rest/api/uom/HmcUser/root%2FJob%2Fx`), when it spells the class
  segment with an escape (`/rest/api/uom/job%73/abc`), or when its identifier
  segment carries `?`, `#` or an encoding of one. The HMC emits none of these,
  and the shapes pinned by `test_a_job_link_is_accepted` are unaffected.
- `hmc_get_job`, `hmc_wait_for_job` and the CLI never reach this guard with an
  unfiltered `job_id`: `src/hmc_mcp/operations/jobs.py:38` refuses `/?#%` first,
  and `src/hmc_mcp/jobs/core.py:184` reaches `wait_for_job_entry` only with an
  HMC-minted identifier and href.
- Residual, unchanged: an `all-targets` grant still reaches a *different* job.
  ADR 0039 accepts that, and this does not revisit it.

## Considered & rejected

- **Record the asymmetry as intentional and leave the code alone.** judgment:
  `_reject_non_job_path`'s stated reason is about the path the request will use,
  which the `job_id` branch builds too, so the record would disagree with the
  guard it describes.
- **Keep matching the percent-decoded form only.** verified: on the installed
  Python, `_JOB_PATH` matches `unquote('/rest/api/uom/HmcUser/root%2FJob%2Fx')`
  but not the raw string, and `httpx.Client(base_url=...).build_request('GET',
  path).url.raw_path` is the raw string — so the guard would approve a path the
  client does not send. judgment: a class guard that inspects a different string
  than the wire carries.
- **Add a character check for `job_id` beside the path check, mirroring
  `_ILLEGAL_JOB_ID_CHARACTERS`.** verified: `src/hmc_mcp/operations/jobs.py:38`
  holds that rule at the operations boundary already. judgment: `_JOB_PATH`
  anchors the identifier segment, so the same site would state one rule twice.
- **Tighten every segment to `[^/?#]`, not just the identifier.** verified: no
  prefix segment can carry a raw `?` or `#` —
  `urlparse('https://h/rest/api/uom/jobs/j?x=1').path` is `/rest/api/uom/jobs/j`
  and the `job_id` branch's prefix is a literal — so it would only refuse an
  encoded one such as `/rest/api/uom/Job%3Fq/Job/j-1`, accepted today. judgment:
  narrowing a shape the issue does not name.
- **Bind the final segment to `job_id` itself.** verified: rejected already, with
  its reasons, in `_reject_non_job_path`'s docstring; unchanged here.
