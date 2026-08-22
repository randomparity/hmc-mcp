# ADR 0064: Caller Token in the LPAR Description

## Status

Accepted

## Context

Issue #358 asks for an optional caller-supplied token that is included in an
LPAR's description field when the LPAR is created ("reserved"), so the LPAR can
be tracked back to the caller's own tracking protocols (ticket systems,
deployment pipelines). ADR 0011 already stamps an ownership token
(`[hmc-mcp owner:<agent_id> created:<date>]`) into the description on create;
that token identifies the *hmc-mcp agent*, not the caller's external tracking
reference. The two serve different audiences: the ownership token is read by
other agents before mutation; the caller token is read by the caller's own
tooling. The issue delegates the token's format to this design.

## Decision

Both `hmc_create_lpar` and `hmc_provision_lpar` (and the CLI `create` path)
accept an optional `caller_token` string. When supplied, the created LPAR's
description carries the ownership stamp followed by a second bracketed
segment:

```
[hmc-mcp owner:<agent_id> created:<YYYY-MM-DD>] [caller <token>]
```

The token grammar is server-defined and enforced before any HMC round trip:
1–64 printable ASCII characters, forbidding whitespace, control characters,
non-ASCII, and the characters `,`, `=`, `"`, `[`, `]`, `\`. The backslash is
refused because ADR 0045 records its behaviour inside an `-i` record as
unverified. This keeps the token a single machine-parseable word, keeps it
writable through the HMC CLI `-i` attribute record (ADR 0045), and keeps the
bracketed token framing unambiguous.

A dedicated extractor, `parse_lpar_ownership_caller_token()`, reads the
segment with its own character class — everything except whitespace and
brackets — so grammar-legal tokens containing `:` are written and parsed
round-trip. It does not reuse the owner regex's narrower class.

Validation lives at two named sites, both before any HMC traffic: the MCP tool
entry points validate first (fast local `ValueError`), and the shared creation
path validates again *outside* the best-effort catch — a malformed token raises
`ValueError` from `create_and_stamp_lpar` instead of being swallowed into a
failed stamp that would discard the ADR 0011 ownership stamp along with the
caller segment. The swallow inside `stamp_lpar_ownership` remains only as the
existing defensive last resort for transport errors.

## Consequences

- Callers can correlate a created LPAR back to their own tracking systems with
  one read (`hmc_get_lpar_description` or the HMC GUI) and no external state.
- The ADR 0011 ownership regex is unaffected: it anchors on the ownership
  stamp's closing bracket, and the caller segment follows it, so ownership
  authorization checks keep working on stamped LPARs that also carry a caller
  token.
- One SSH round trip total, unchanged from ADR 0011; the caller segment adds no
  extra HMC traffic.
- A malformed token is rejected locally with a `ValueError` naming the offending
  character, before name-uniqueness checks or any HMC call.
- The caller token is advisory metadata, like the ownership stamp: nothing
  enforces its uniqueness or truthfulness, and it grants no authority.
- Whether the HMC caps or silently truncates long descriptions is unverified;
  `validate_lpar_description` bounds charset but not length. A truncating HMC
  would silently drop tracking data while the stamp still reports success. The
  64-character token cap bounds the added length; no length guard is added.
- Adding a parameter to two public lifecycle tools triggers ADR 0016's rule
  that schema contract tests must be updated in the same change.
- `hmc_deploy_partition_template` stamping (ADR 0014) does not carry a caller
  token; template deployments have no caller token parameter to pass.

## Considered & rejected

- **Fold the caller token into the ownership stamp's brackets.**
  verified: the ADR 0011 parse regex
  (`hmc_mcp/operations_lpar.py:_OWNERSHIP_TOKEN`) anchors `]` immediately after
  `created:<date>`; text inside the brackets makes every stamped description
  fail the ownership parse, and `_authorize_lpar_ownership_description`
  (operations_lpar.py:141-156) then fails closed — raising `PermissionError`
  for *any* mutation, including by the legitimate owner — rather than
  downgrading to "no claim".
- **Compose the caller token into the initial create payload** (REST
  `Description` element / `mksyscfg description=`).
  judgment: it would remove the post-create window for the caller segment, but
  it must be threaded through two divergent create paths (REST document and the
  406 CLI fallback), duplicating description plumbing that the shared stamp
  already owns; and the adopted design retains the same narrow race window the
  do-nothing option has — the stamp is a post-create write either way, a
  residual ADR 0011 accepted for the ownership token.
- **Free-form caller text appended to the description.**
  judgment: unparseable prose defeats the tracking purpose — the issue asks for
  machine-trackable provenance — and free text reaching the `-i` record is the
  injection class ADR 0045 closed.
- **A separate HMC user-defined attribute field.**
  judgment: no second writable free-text attribute for partitions is known in
  the `mksyscfg`/`chsyscfg` surface this server uses, and the claim traces only
  to ADR 0011's own record — it cannot be verified without a live HMC. ADR 0011
  already established the description field as the only per-LPAR metadata
  writable over SSH with no external dependency.
- **Write the caller token via a second SSH call after the ownership stamp.**
  judgment: doubles the best-effort round trips and introduces a half-stamped
  state (ownership written, caller segment lost) that the single composed write
  avoids.
- **Do nothing; callers can set the description after create with
  `hmc_set_lpar_description`.**
  rejected per issue #358: the token must be included *upon reservation*, and a
  post-create write by the caller is an extra tool call with a wider race
  window than the stamp this design already performs.

