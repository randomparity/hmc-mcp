# Validated job-href design

## Goal

Make the `job_href` accepted by `operations.jobs.get_job` and `wait_for_job` exactly the value
those operations return, without changing the persisted-handle shape established by ADR 0093 or
the path-equivalence and wait-timing behavior delivered by issues #529 and #532.

## Decision

Reject any caller-supplied `job_href` containing TAB (`U+0009`), LF (`U+000A`), or CR (`U+000D`)
in `_clean_job_href`, before `urlparse` can delete those characters. Continue trimming ordinary
surrounding whitespace and echo the resulting cleaned string when its request resolves. This keeps
the existing contract: callers may persist the same relative or absolute link spelling they
submitted, while every character in that returned spelling was present when validation began.

Normalizing the handle to the parsed path is not needed. It would discard the caller's accepted
absolute/relative spelling, host, query, and fragment and would amend the persisted representation
for all valid existing handles to solve three invalid control-character cases.

## Components and flow

`operations.jobs._clean_job_href` remains the single operations-layer boundary shared by
`get_job` and `wait_for_job`. It treats blank input as absent, rejects TAB/CR/LF with `ValueError`,
and otherwise returns the trimmed link. The existing call into `HMCClient.get_job` parses and
validates that same returned string; `_select_persisted_job_href` echoes it only after a successful
read. No client request, poll timing, stale-link comparison, or response normalization changes.

The served `hmc_wait_for_job` documentation will state that an echoed link is the validated input
whose path was requested. The previous warning that control characters are unchecked is removed.
The changelog will call out the new served-tool rejection.

## Error behavior

TAB, LF, or CR anywhere in a non-null `job_href`, including at either edge, raises `ValueError`
before the client request. The message names `job_href`, the rejected control-character class, and
the need to pass the HMC link without embedded controls. `None` and whitespace-only strings retain
their existing absent-link behavior. Other characters retain existing client path validation.

## Compatibility

This is an intentional rejection of inputs that `urlparse` changed before validation. Valid
persisted handles keep their exact post-trim spelling and request path. The implementation must not
change `_select_persisted_job_href`, `_read_job`, `wait_for_job` scheduling, or the #529 comparison
of stale and rediscovered links by parsed resource path.

## Security model

- Boundary: a library or MCP caller supplies `job_href` to the operations layer. The caller may be
  an authenticated tool user or a process restoring an untrusted persisted value.
- Control: `_clean_job_href` rejects the three ASCII controls `urllib.parse` deletes before the
  client sees them; the client's existing `_reject_non_job_path` continues to constrain the parsed
  resource path. The error uses a fixed message and does not echo the rejected value.
- Trust: the design does not attest the host, query, or fragment because only the validated path is
  requested. It preserves the existing all-targets authorization model and does not add a reachable
  operation.
- Out of scope: changing direct, unsupported `HMCClient.get_job` semantics; URL canonicalization;
  authorization changes; and filtering other control characters already rejected by path checks.

## Tests

- Parameterize TAB, CR, and LF through each public operation and assert `ValueError` before any
  client call.
- On the served wait tool, assert those three inputs raise and retain the existing successful echo
  case for a valid link.
- Keep the existing #529 relative/absolute stale-link tests and #532 fake-clock timing tests green.
- Regenerate tool documentation after simplifying the served docstring.

## Acceptance criteria

1. No TAB, CR, or LF-bearing `job_href` reaches URL parsing or an HMC request through either job
   polling operation.
2. Every accepted supplied `job_href` returned in a `JobOutcome` is the same cleaned string that
   was passed to client path validation.
3. The served tool describes the validated echo without the obsolete mismatch caveat.
4. Existing path-equivalence and confirming-read timing behavior remains unchanged and tested.

## Decision record

This design amends [ADR 0093](../../adr/0093-cross-process-job-polling-contract.md), which owns the
persisted two-string job handle and its exact-spelling echo rule.
