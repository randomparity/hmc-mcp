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
non-ASCII, and the record delimiters and format characters `,`, `=`, `"`,
`[`, `]`. This keeps the token a single machine-parseable word, keeps it
writable through the HMC CLI `-i` attribute record (ADR 0045), and keeps the
bracketed token framing unambiguous. A new `parse_lpar_ownership_caller_token()`
extracts the token from a description, mirroring `parse_lpar_ownership_owner()`.

The combined description is written in the same single best-effort SSH stamp
call ADR 0011 already makes: a stamp failure warns in the tool result and never
fails the create. Omitting `caller_token` produces exactly today's description.

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
  character, before name-uniqueness checks or any HMC call — the same fail-fast
  layering `set_lpar_description` uses.
- The caller token is advisory metadata, like the ownership stamp: nothing
  enforces its uniqueness or truthfulness, and it grants no authority.
- `hmc_deploy_partition_template` stamping (ADR 0014) does not carry a caller
  token; template deployments have no caller token parameter to pass.

## Considered & rejected

- **Fold the caller token into the ownership stamp's brackets.**
  verified: the ADR 0011 parse regex
  (`hmc_mcp/operations_lpar.py:_OWNERSHIP_TOKEN`) anchors `]` immediately after
  `created:<date>`; text inside the brackets would make every stamped
  description fail the ownership parse and degrade foreign-owner authorization
  to "no claim".
- **Free-form caller text appended to the description.**
  judgment: unparseable prose defeats the tracking purpose — the issue asks for
  machine-trackable provenance — and free text reaching the `-i` record is the
  injection class ADR 0045 closed.
- **A separate HMC user-defined attribute field.**
  verified: `mksyscfg`/`chsyscfg` expose no second writable free-text attribute
  for partitions in the commands this server uses (`lssyscfg` reads
  `description` only); the description field is the only per-LPAR metadata
  writable over SSH with no external dependency (ADR 0011).
- **Write the caller token via a second SSH call after the ownership stamp.**
  judgment: doubles the best-effort round trips and introduces a half-stamped
  state (ownership written, caller segment lost) that the single composed write
  avoids.
- **Do nothing; callers can set the description after create with
  `hmc_set_lpar_description`.**
  rejected per issue #358: the token must be included *upon reservation*, and a
  post-create write is a second tool call plus a race window in which another
  actor may act on the unstamped partition.
