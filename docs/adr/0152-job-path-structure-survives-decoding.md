# ADR 0152: Preserve job-path structure across decoding

## Status

Accepted (2026-09-16)

## Context

Issue #837 closes the decoded-only path-class residual recorded in ADR 0149.
The request uses the raw path, while the existing guard checks a decoded path.
A decode can change separators or introduce query/fragment delimiters in a
prefix segment, changing which resource the path identifies. Both public job
methods already share one guard and explicitly name the originating argument.

## Decision

Retain that shared guard and require the raw and once-percent-decoded paths to
fully match the job-resource grammar. Exclude query and fragment delimiters
from every segment, and reject a decode that adds path separators. Do not
rewrite the request or bind the link's identifier to `job_id`.

The legacy `Job` and operation/global `jobs` collections remain supported.
Percent-encoded non-structural data remains accepted when both forms meet the
grammar. Encoded collection spelling and encoded separators are refused rather
than relying on transport/server interpretation to agree.

## Consequences

- `get_job_entry` and `delete_job` refuse ambiguous paths before transport,
  retaining the explicit `job_id` or `job_href` diagnostic (issue #847).
- Actual href queries/fragments are still removed by the existing parser;
  decoded delimiters inside the resulting path are refused.
- This amends ADR 0149's decoded-only policy and closes its #837 residual;
  its shared ownership and argument-origin decisions remain in force.
- Different-job access remains accepted under ADR 0039's all-targets grant.
  URL-parser control stripping remains owned by #537.

## Considered & rejected

- **Keep decoded-only matching.** verified: `core.py` at 0790b7bf checks
  `unquote(path)` alone while both job consumers send `path` unchanged.
- **Match both complete paths without preserving separators.** judgment:
  two valid class suffixes do not require the same segment boundaries.
- **Canonicalize and send the decoded path.** judgment: changing the request's
  meaning is less conservative than refusing ambiguous links.
- **Reject every percent escape.** judgment: unnecessary loss of harmless
  encoded data whose structure and job class remain unchanged.
