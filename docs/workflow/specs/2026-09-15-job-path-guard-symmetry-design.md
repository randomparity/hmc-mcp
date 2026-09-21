# Guard the job request path wherever it is built

Issue #825. Decision: [ADR 0149](../../adr/0149-one-job-path-expression-guarded-once.md).

## Problem

`get_job_entry` and `delete_job` interpolate `job_id` into the same
`/rest/api/uom/jobs/{job_id}` path and only `delete_job` refuses the result.
`_JOB_PATH`'s identifier segment is `[^/]+`, which admits `?` and `#`, so neither
method refuses a `job_id` that appends a query to the request or truncates it to a
different job. ADR 0149 holds the evidence and the decision.

## Scope

`src/hmc_mcp/client/core.py`: tighten `_JOB_PATH`'s identifier segment to
`[^/?#]`; give `_reject_non_job_path` a second parameter naming the refused
argument, defaulting to `job_href`, and a remedy sentence covering both; collapse
`get_job_entry`'s branch into the two lines `delete_job` uses. New cases in
`tests/unit/test_client.py`. ADR 0149. Nothing else. Ownership stands —
`_reject_non_job_path` already owns job-path class refusal at the site that builds
the path (ADR 0143 places segment grammar there) — so this is a clean extension:
nothing migrates, no path becomes obsolete. The parameter default is what keeps
`tests/unit/test_request_path_safety.py`, read but not owned here, calling the
guard with one argument unedited. Out of scope, with owners: dot-segment
re-guarding (`_reject_dot_segments`, no owner); whether an `all-targets` grant may
reach a different job (ADR 0039); query and fragment hardening for the other UOM
path builders (no owner).

## Failure model

- **Actors and deployments.** A library consumer importing `hmc_mcp.api` and
  calling `get_job_entry`, `wait_for_job_entry` or `delete_job` with values it
  stored and read back; the `hmc-mcp` CLI and the stdio MCP server, which reach
  those methods through `src/hmc_mcp/operations/jobs.py` and, for the
  submit-and-wait tools, `src/hmc_mcp/jobs/core.py:184` with an HMC-minted
  identifier and href.
- **Invariants and assets at stake.** The path a tool classified `read`/`job` or
  `delete`/`job` sends stays inside the job resource class; `delete_job` is
  destructive, so a path leaving that class, or silently truncated to a different
  job, is not recoverable.
- **Accepted failure classes.** A `job_id` whose percent-encoding decodes to `/`,
  `?` or `#` is refused, unless that decode itself ends in a job tail, although
  httpx would have sent it literally — accepted:
  `src/hmc_mcp/operations/jobs.py:38` already refuses `%` in a `job_id` and no
  HMC-minted UUID or JobID carries one. A crafted `job_href` or `job_id` whose
  path is a job path only after decoding (`/rest/api/uom/HmcUser/root%2FJob%2Fx`,
  `x%2FJob%2Fy`) is still accepted by the guard while httpx sends the raw string —
  accepted here with its cost bounded and stated: for a server that splits the
  query before percent-decoding the path, neither shape reaches the `HmcUser`
  record; a server that decodes first is outside that bound on a single decode
  (ADR 0149 names the case), and the caller already holds the `all-targets` grant
  ADR 0039 requires. No completion criterion sources
  closing it, so it stays an open residual recorded in ADR 0149 rather than work
  this change absorbs. A character in a
  `job_id` outside `/`, `?` and `#` still reaches `_request` (ADR 0148) —
  accepted: this closes a class boundary, not a character allowlist. Reaching a
  *different* job under an `all-targets` grant — accepted under ADR 0039.
- **Covered elsewhere.** Dot segments: `_reject_dot_segments` (ADR 0039).
  Unbuildable URLs: `_request` (ADR 0148). `job_id` and `job_href` hygiene at the
  tool boundary: `src/hmc_mcp/operations/jobs.py` (ADR 0093).

## Threat model

- **Boundaries.** None added; one narrowed — caller-supplied `job_id` and
  `job_href` values entering job path construction in `core.py`. The `job_href`
  branch moves only for an identifier segment decoding to `?` or `#`.
- **Actors.** The library consumer named above is the only one reaching this
  guard with an unfiltered `job_id`; the HMC supplies the SELF link.
- **Control.** `_reject_non_job_path` refuses unless `unquote(path)` ends in `Job`
  or `jobs` followed by one identifier segment free of `?` and `#`. Refuse, never
  repair; the message names the argument and the class, never the path.
- **Out of scope.** The three exclusions listed under Scope, plus the decode
  residual accepted above.

## Success

1. `get_job_entry` refuses a `job_id` of `""`, `a/b` or `a%2Fb` with `HMCError`,
   sending no request.
2. `get_job_entry` and `delete_job` each refuse a `job_id` of `j?x=1`, `j#f`,
   `j%3Fx=1` or `j%23f` with `HMCError`, sending no request.
3. The message names `job_id` when the path came from `job_id`, `job_href` when
   it came from `job_href`.
4. Each `job_href` shape pinned by
   `tests/unit/test_request_path_safety.py::test_a_job_link_is_accepted`, and a
   clean `job_id`, still reach the wire from `get_job_entry`, `delete_job` and
   `wait_for_job_entry`.
5. ADR 0149 exists and `just adr-numbering` passes.

## Validation

Every criterion is machine-checkable; the plan's Verification inventory holds each
one's mode, test, expected red and green command, in `tests/unit/test_client.py`
for criteria 1-4 and `just adr-numbering` for criterion 5.
